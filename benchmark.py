#!/usr/bin/env python3
"""
Versión del benchmark que usa un flujo más automático de function calling:
- envía herramientas a Ollama,
- espera tool calls del modelo,
- ejecuta automáticamente las acciones en Home Assistant,
- y valida el estado final.
"""

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil
import requests
import paho.mqtt.client as mqtt

import gpu_metrics
from check_ha_states import get_ha_headers, validate_single_element
from device_info import get_device_name
from logger import configure_logging, get_logger

logger = get_logger(__name__)


BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = BASE_DIR / "data"
CONFIG_PATH = DATA_DIR / "config.json"
OLLAMA_API_URL = "http://localhost:11434/api/chat"
DEVICE_NAME = os.getenv("DEVICE_NAME", get_device_name())
OUTPUT_CSV = BASE_DIR / "benchmark_results_master.csv"

with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
    CONFIG = json.load(fh)

HA_API_URL = os.getenv("HA_URL", CONFIG.get("ha_url", "http://localhost:8123"))
HA_API_PASSWORD = os.getenv("HA_API_PASSWORD", CONFIG.get("ha_api_key", ""))
MODEL_NAME = os.getenv("OLLAMA_MODEL", CONFIG.get("model_name", "qwen2.5:1.5b"))
MQTT_BROKER = CONFIG.get("mqtt_broker", "192.168.1.100")
MQTT_PORT = 1883
MQTT_TOPIC_POWER = CONFIG.get("mqtt_topic_power", "tele/sonoff_pow/SENSOR")
current_power_watts = 0.0


# --- MQTT power reader ---
def on_mqtt_message(client, userdata, msg):
    global current_power_watts
    try:
        payload = json.loads(msg.payload.decode("utf-8"))
        if "ENERGY" in payload and "Power" in payload["ENERGY"]:
            current_power_watts = float(payload["ENERGY"]["Power"])
        elif "power" in payload:
            current_power_watts = float(payload["power"])
        elif "apower" in payload:
            current_power_watts = float(payload["apower"])
    except Exception:
        pass


def start_mqtt_listener():
    client = mqtt.Client()
    client.on_message = on_mqtt_message
    try:
        logger.debug("MQTT conectando a %s:%s topic=%s", MQTT_BROKER, MQTT_PORT, MQTT_TOPIC_POWER)
        client.connect(MQTT_BROKER, MQTT_PORT, 60)
        client.subscribe(MQTT_TOPIC_POWER)
        client.loop_start()
    except Exception as exc:
        logger.warning("MQTT no disponible: %s", exc)


# --- System metrics ---
def get_cpu_temperature():
    try:
        # Linux/macOS: psutil.sensors_temperatures() solo existe en estas plataformas
        if hasattr(psutil, "sensors_temperatures"):
            temps = psutil.sensors_temperatures()
            if temps:
                for entries in temps.values():
                    for entry in entries:
                        return entry.current
        # Linux: fallback a sysfs
        if sys.platform.startswith("linux"):
            with open("/sys/class/thermal/thermal_zone0/temp", "r", encoding="utf-8") as f:
                return float(f.read().strip()) / 1000.0
        # Windows: intentar WMI (MSAcpi_ThermalZoneTemperature) vía PowerShell
        if sys.platform.startswith("win"):
            result = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "(Get-CimInstance -ClassName MSAcpi_ThermalZoneTemperature -Namespace root/wmi | Select-Object -First 1).CurrentTemperature",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                # WMI devuelve décimas de Kelvin
                return (float(result.stdout.strip()) / 10.0) - 273.15
    except Exception:
        logger.error("Error al obtener temperatura de CPU", exc_info=True)
    return -1.0


def get_gpu_metrics():
    """GPU utilization (%) and used GPU memory (MB).

    Uses gpu_metrics, which reads nvidia-smi on discrete NVIDIA GPUs and
    falls back to sysfs on Jetson boards (Orin Nano, etc.) where nvidia-smi
    is not available.
    """
    metrics = gpu_metrics.get_gpu_metrics()
    if metrics is None:
        return 0.0, 0.0
    return (metrics.get("util_gpu") or 0.0), (metrics.get("mem_used_mb") or 0.0)


BATTERY_POWER_CACHE_TTL = 1.0
_battery_power_cache = {"sampled_at": 0.0, "watts": None}


def _read_battery_power_watts_linux():
    """Potencia de batería (W) leyendo sysfs de Linux. None si no hay batería."""
    for power_dir in ("BAT0", "BAT1", "BAT2", "BAT3"):
        base = f"/sys/class/power_supply/{power_dir}"
        try:
            with open(f"{base}/status", "r", encoding="utf-8") as f:
                status = f.read().strip()
        except Exception:
            continue

        power_uw = None
        try:
            with open(f"{base}/power_now", "r", encoding="utf-8") as f:
                power_uw = float(f.read().strip())
        except Exception:
            try:
                with open(f"{base}/current_now", "r", encoding="utf-8") as f:
                    current_ua = float(f.read().strip())
                with open(f"{base}/voltage_now", "r", encoding="utf-8") as f:
                    voltage_uv = float(f.read().strip())
                # µA * µV = µW
                power_uw = current_ua * voltage_uv
            except Exception:
                continue

        watts = power_uw / 1e6
        if status == "Charging":
            return -abs(watts)
        if status in ("Full", "Not charging"):
            return 0.0
        return abs(watts)
    return None


def _read_battery_power_watts_windows():
    """Potencia de batería (W) vía WMI/ACPI en Windows. None si no hay batería.

    Primero intenta BatteryStatus (root/wmi), que expone ChargeRate/DischargeRate
    en mW; si no, estima desde Win32_Battery (DesignCapacity / EstimatedRunTime).
    """
    try:
        script_rate = (
            "$b = Get-CimInstance -Namespace root/wmi -ClassName BatteryStatus | Select-Object -First 1; "
            "if ($b -and -not $b.PowerOnline -and $b.DischargeRate -gt 0) { [double]$b.DischargeRate } "
            "elseif ($b -and $b.PowerOnline -and $b.ChargeRate -gt 0) { -([double]$b.ChargeRate) } "
            "elseif ($b) { 0.0 }"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script_rate],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip()) / 1000.0

        script_estimate = (
            "$bat = Get-CimInstance -ClassName Win32_Battery | Select-Object -First 1; "
            "if ($bat -and $bat.DesignCapacity -and $bat.EstimatedRunTime -gt 0 -and $bat.EstimatedChargeRemaining -gt 0) { "
            "  [math]::Round($bat.DesignCapacity * $bat.EstimatedChargeRemaining / 100.0 * 60.0 / ($bat.EstimatedRunTime * 1000.0), 3) "
            "}"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script_estimate],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except Exception:
        pass
    return None


def _read_battery_power_watts_darwin():
    """Potencia de batería (W) en macOS leyendo IOKit con ioreg. None si no hay batería.

    Amperage está en mA (positivo al descargar, negativo al cargar) y Voltage en mV.
    """
    try:
        result = subprocess.run(
            ["ioreg", "-r", "-n", "AppleSmartBattery"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode != 0:
            return None
        amperage_ma = None
        voltage_mv = None
        for line in result.stdout.splitlines():
            match = re.search(r'"Amperage"\s*=\s*(-?\d+)', line)
            if match:
                amperage_ma = int(match.group(1))
                continue
            match = re.search(r'"Voltage"\s*=\s*(\d+)', line)
            if match:
                voltage_mv = int(match.group(1))
        if amperage_ma is not None and voltage_mv is not None:
            return (amperage_ma / 1000.0) * (voltage_mv / 1000.0)
    except Exception:
        pass
    return None


def get_battery_power_watts():
    """Devuelve la potencia actual de la batería en watts.

    Positivo = consumo (descarga), negativo = recarga, 0.0 = enchufada sin cambios,
    None = no disponible (p. ej. PC de escritorio sin batería).
    El resultado se cachea 1 segundo para no lanzar subprocesos en cada muestra.
    """
    now = time.time()
    cache = _battery_power_cache
    if now - cache["sampled_at"] <= BATTERY_POWER_CACHE_TTL:
        return cache["watts"]

    watts = None
    try:
        if sys.platform.startswith("linux"):
            watts = _read_battery_power_watts_linux()
        elif sys.platform == "darwin":
            watts = _read_battery_power_watts_darwin()
        elif sys.platform.startswith("win"):
            watts = _read_battery_power_watts_windows()
    except Exception:
        logger.error("Error al obtener la potencia de la batería", exc_info=True)

    cache["sampled_at"] = now
    cache["watts"] = watts
    return watts


def _collect_current_metrics():
    return {
        "cpu_usage_pct": psutil.cpu_percent(interval=None),
        "ram_usage_mb": round(psutil.virtual_memory().used / (1024 * 1024), 2),
        "gpu_usage_pct": 0.0,
        "vram_usage_mb": 0.0,
        "power_watts": current_power_watts,
        "cpu_temp_c": round(get_cpu_temperature(), 2),
        "battery_power_watts": get_battery_power_watts(),
    }


def start_metrics_monitor(device_name, sample_interval=1.0, baseline_ram_mb=None):
    """Inicia un hilo separado que captura métricas mientras dura la ejecución."""
    metrics_by_device_and_second = {}
    stop_event = threading.Event()

    def monitor_loop():
        while not stop_event.is_set():
            second_key = int(time.time())
            snapshot = _collect_current_metrics()
            gpu_pct, vram_mb = get_gpu_metrics()
            snapshot["gpu_usage_pct"] = gpu_pct
            snapshot["vram_usage_mb"] = vram_mb
            metrics_by_device_and_second.setdefault(device_name, {})[second_key] = snapshot
            logger.debug(
                "Métricas [%s] segundo=%d: %s",
                device_name,
                second_key,
                json.dumps(snapshot, ensure_ascii=False),
            )
            time.sleep(sample_interval)

    thread = threading.Thread(target=monitor_loop, daemon=True)
    thread.start()
    return thread, stop_event, metrics_by_device_and_second


def stop_metrics_monitor(thread, stop_event, metrics_by_device_and_second, device_name, baseline_ram_mb=None):
    """Detiene el hilo y devuelve el máximo de cada métrica capturada."""
    stop_event.set()
    if thread.is_alive():
        thread.join(timeout=2)

    per_device_metrics = metrics_by_device_and_second.get(device_name, {})
    if not per_device_metrics:
        per_device_metrics = {int(time.time()): _collect_current_metrics()}

    snapshots = list(per_device_metrics.values())
    ram_peak_mb = max(item["ram_usage_mb"] for item in snapshots)
    if baseline_ram_mb is not None:
        ram_peak_mb = max(0.0, ram_peak_mb - baseline_ram_mb)

    battery_power_levels = [item["battery_power_watts"] for item in snapshots if item["battery_power_watts"] is not None]
    battery_power_watts = max(battery_power_levels) if battery_power_levels else None

    return {
        "device_name": device_name,
        "metrics_by_device_and_second": metrics_by_device_and_second,
        "cpu_usage_pct": max(item["cpu_usage_pct"] for item in snapshots),
        "ram_usage_mb": ram_peak_mb,
        "gpu_usage_pct": max(item["gpu_usage_pct"] for item in snapshots),
        "vram_usage_mb": max(item["vram_usage_mb"] for item in snapshots),
        "power_watts": max(item["power_watts"] for item in snapshots),
        "cpu_temp_c": max(item["cpu_temp_c"] for item in snapshots),
        "battery_power_watts": battery_power_watts,
    }


# --- Home Assistant execution ---
def execute_ha_command(parsed_cmd, ha_url=HA_API_URL, api_password=HA_API_PASSWORD):
    status = parsed_cmd.get("status")
    action = parsed_cmd.get("action")
    device_id = parsed_cmd.get("device_id")
    parameter = parsed_cmd.get("parameter")

    if status != "success" or not device_id or device_id in ("null", "multiple", None):
        return False, "Omisión (inválido o ambiguo)"

    headers = {"Content-Type": "application/json"}
    configured_token = (api_password or "").strip()

    if configured_token:
        headers["Authorization"] = f"Bearer {configured_token}"
    else:
        headers["X-HA-Provider"] = "trusted_networks"

    domain = device_id.split(".")[0] if "." in str(device_id) else "homeassistant"

    try:
        if action in ("turn_on", "turn_off", "lock", "unlock"):
            url = f"{ha_url}/api/services/{domain}/{action}"
            payload = {"entity_id": device_id}
            res = requests.post(url, headers=headers, json=payload, timeout=5)
            logger.debug("HA respuesta ejecución %s %s:\n%s", action, device_id, res.text)

        elif action == "set_value":
            if domain == "climate":
                url = f"{ha_url}/api/services/climate/set_temperature"
                try:
                    temp_val = float(parameter)
                except (TypeError, ValueError):
                    temp_val = 21.0
                payload = {"entity_id": device_id, "temperature": temp_val}
            elif domain == "input_number":
                url = f"{ha_url}/api/services/input_number/set_value"
                try:
                    val = float(parameter)
                except (TypeError, ValueError):
                    val = 0.0
                payload = {"entity_id": device_id, "value": val}
            else:
                url = f"{ha_url}/api/services/{domain}/set_value"
                payload = {"entity_id": device_id, "value": parameter}
            res = requests.post(url, headers=headers, json=payload, timeout=5)
            logger.debug("HA respuesta ejecución %s %s:\n%s", action, device_id, res.text)

        elif action == "get_state":
            url = f"{ha_url}/api/states/{device_id}"
            res = requests.get(url, headers=headers, timeout=5)
            logger.debug("HA respuesta consulta %s:\n%s", device_id, res.text)
            if res.status_code == 200:
                state = res.json().get("state", "unknown")
                return True, f"Estado: {state}"
            return False, f"HA HTTP Error {res.status_code}"

        else:
            return False, f"Acción desconocida '{action}'"

        if res.status_code in (200, 201):
            return True, "Ejecutado con éxito"
        return False, f"HA Error HTTP {res.status_code}"

    except requests.exceptions.RequestException as exc:
        return False, f"Error conexión HA: {exc}"


# --- Ollama interaction ---
def parse_tool_calls(message_obj):
    tool_calls = message_obj.get("tool_calls", [])
    action_map = {
        "turn_on_device": "turn_on",
        "turn_off_device": "turn_off",
        "lock_device": "lock",
        "unlock_device": "unlock",
        "set_temperature": "set_value",
        "set_device_value": "set_value",
        "get_device_state": "get_state",
    }

    def _coerce_arguments(args):
        if isinstance(args, str):
            try:
                parsed = json.loads(args)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass
            return {}
        if isinstance(args, dict):
            return args
        return {}

    if not tool_calls:
        content = (message_obj.get("content") or "").strip()
        if not content:
            return False, None, None, None, "failure", []

        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                return True, parsed.get("device_id", "null"), parsed.get("action", "null"), parsed.get("parameter"), parsed.get("status", "failure"), []
        except json.JSONDecodeError:
            pass

        function_call_match = re.match(r"^(\w+)\s*\(\s*(\{.*\})\s*\)$", content, re.DOTALL)
        if function_call_match:
            func_name = function_call_match.group(1)
            args_text = function_call_match.group(2)
            try:
                args = json.loads(args_text)
            except json.JSONDecodeError:
                args = {}
            if isinstance(args, dict):
                device_id = args.get("device_id", "null")
                parameter = args.get("parameter") or args.get("temperature") or args.get("value")
                action = action_map.get(func_name, func_name)
                return True, device_id, action, parameter, "success", []

        # Plain-text status responses: model returns "indeterminado" or "fallo"
        # when the prompt is ambiguous, out-of-domain, or has no valid device.
        # Map both to "indeterminado" to match expected_status in test-prompts.json.
        content_lower = content.lower()
        if content_lower in ("indeterminado", "fallo"):
            return True, "null", "null", None, "indeterminado", []

        return False, "null", "null", None, "failure", []

    if len(tool_calls) > 1:
        return True, "multiple", "multiple", None, "success", tool_calls

    tool_call = tool_calls[0]
    function = tool_call.get("function", {})
    func_name = function.get("name", "")
    args = _coerce_arguments(function.get("arguments", {}) or {})

    device_id = args.get("device_id", "null")
    parameter = args.get("parameter") or args.get("temperature") or args.get("value")
    action = action_map.get(func_name, func_name)
    return True, device_id, action, parameter, "success", tool_calls


def call_ollama(prompt_text, system_prompt, tools_definitions):
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt_text},
        ],
        "tools": tools_definitions,
        "stream": False,
        "options": {"temperature": 0.0},
    }

    response = requests.post(OLLAMA_API_URL, json=payload, timeout=60)
    if response.status_code != 200:
        raise RuntimeError(f"Ollama devolvió HTTP {response.status_code}: {response.text}")

    data = response.json()
    message_obj = data.get("message", {})
    raw_response = json.dumps(message_obj, ensure_ascii=False)
    model_reasoning = message_obj.get("thinking", "") or ""
    eval_count = data.get("eval_count", 0)
    eval_duration = data.get("eval_duration", 1) / 1e9
    tokens_per_sec = round(eval_count / eval_duration, 2) if eval_duration > 0 else 0.0

    return {
        "message_obj": message_obj,
        "raw_response": raw_response,
        "model_reasoning": model_reasoning,
        "tokens_generated": eval_count,
        "tokens_per_second": tokens_per_sec,
    }


def warmup_model():
    """Hace una llamada de precarga a Ollama para que el modelo quede cargado en memoria
    antes de iniciar el benchmark, evitando que la primera llamada real tarde más de la cuenta."""
    try:
        logger.info("Precargando modelo %s en Ollama...", MODEL_NAME)
        payload = {
            "model": MODEL_NAME,
            "messages": [
                {"role": "user", "content": "Hola"},
            ],
            "stream": False,
            "options": {"temperature": 0.0},
        }
        response = requests.post(OLLAMA_API_URL, json=payload, timeout=180)
        if response.status_code != 200:
            logger.warning("Warm-up falló: Ollama devolvió HTTP %s", response.status_code)
            return False
        logger.info("Modelo %s precargado correctamente.", MODEL_NAME)
        return True
    except requests.exceptions.RequestException as exc:
        logger.warning("Warm-up no disponible: %s", exc)
        return False


# --- Benchmark loop ---
def parse_args():
    parser = argparse.ArgumentParser(description="Run the Home Assistant function-calling benchmark")
    parser.add_argument(
        "--model",
        default=os.getenv("OLLAMA_MODEL", "qwen2.5:1.5b"),
        help="Ollama model name to use (default: env OLLAMA_MODEL or qwen2.5:1.5b)",
    )
    parser.add_argument(
        "--device-name",
        default=os.getenv("DEVICE_NAME", get_device_name()),
        help="Device name to record in the benchmark results (default: detected hardware model)",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["debug", "info", "warning", "error", "critical"],
        help="Logging level (default: env LOG_LEVEL, config.json, or INFO)",
    )
    parser.add_argument(
        "--prompt-id",
        default=None,
        help="ID del test prompt a ejecutar (ej: CMD_001). Si no se especifica, ejecuta todos.",
    )
    return parser.parse_args()


def run_benchmark(model_name=None, device_name=None, prompt_id=None):
    global MODEL_NAME, DEVICE_NAME
    if model_name is not None:
        MODEL_NAME = model_name
    if device_name is not None:
        DEVICE_NAME = device_name
    start_mqtt_listener()

    with open(DATA_DIR / "tools-definition.json", "r", encoding="utf-8") as fh:
        tools_definitions = json.load(fh)
    with open(DATA_DIR / "test-prompts.json", "r", encoding="utf-8") as fh:
        test_prompts = json.load(fh)
    with open(DATA_DIR / "system-prompt.md", "r", encoding="utf-8") as fh:
        system_prompt = fh.read()

    if prompt_id:
        filtered = [item for item in test_prompts if item["id"] == prompt_id]
        if not filtered:
            available_ids = ", ".join(item["id"] for item in test_prompts)
            logger.error("No se encontró el prompt con ID '%s'. IDs disponibles: %s", prompt_id, available_ids)
            sys.exit(1)
        test_prompts = filtered
        logger.info("Ejecutando solo el prompt: %s", prompt_id)

    file_exists = OUTPUT_CSV.exists()
    with open(OUTPUT_CSV, mode="a", newline="", encoding="utf-8") as csv_file:
        fieldnames = [
            "timestamp",
            "hardware_device",
            "model_name",
            "prompt_id",
            "prompt_type",
            "latency_sec",
            "tokens_generated",
            "tokens_per_second",
            "ram_baseline_mb",
            "ram_usage_mb",
            "vram_usage_mb",
            "cpu_usage_pct",
            "gpu_usage_pct",
            "power_watts",
            "cpu_temp_c",
            "battery_power_watts",
            "json_valid",
            "ha_executed",
            "ha_state_valid",
            "model_reasoning",
            "raw_response",
        ]
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()

        logger.info("==================================================")
        logger.info(" BENCHMARK FUNCTION CALLING AUTOMÁTICO")
        logger.info("==================================================")

        baseline_ram_mb = round(psutil.virtual_memory().used / (1024 * 1024), 2)
        logger.debug("RAM línea base inicial: %s MB", baseline_ram_mb)

        warmup_model()

        ha_validation_headers = get_ha_headers(bearer_token=HA_API_PASSWORD)
        ha_validation_cache = {}

        for item in test_prompts:
            prompt_id = item["id"]
            prompt_text = item["prompt"]
            prompt_type = item["type"]

            logger.info("Ejecutando %s: %s", prompt_id, prompt_text)
            t_start = time.time()
            try:
                metrics_thread, metrics_stop_event, metrics_by_device_and_second = start_metrics_monitor(
                    DEVICE_NAME,
                    sample_interval=0.001,
                    baseline_ram_mb=baseline_ram_mb,
                )
                logger.debug("______________Métricas monitor iniciadas para %s", DEVICE_NAME)
                result = call_ollama(prompt_text, system_prompt, tools_definitions)
                latency_sec = round(time.time() - t_start, 4)
                logger.debug("____________Respuesta del modelo (%s): %s", prompt_id, result["raw_response"])

                message_obj = result["message_obj"]
                is_valid, model_device, model_action, model_param, model_status, tool_calls_list = parse_tool_calls(message_obj)

                device_correct = str(model_device).lower() == str(item["expected_device"]).lower()
                status_correct = str(model_status).lower() == str(item["expected_status"]).lower()
                json_valid = is_valid and device_correct and status_correct

                ha_executed = False
                ha_state_valid = "N/A"

                if tool_calls_list:
                    for tool_call in tool_calls_list:
                        function = tool_call.get("function", {})
                        func_name = function.get("name", "")
                        args = function.get("arguments", {}) or {}
                        action = {
                            "turn_on_device": "turn_on",
                            "turn_off_device": "turn_off",
                            "lock_device": "lock",
                            "unlock_device": "unlock",
                            "set_temperature": "set_value",
                            "set_device_value": "set_value",
                            "get_device_state": "get_state",
                        }.get(func_name, func_name)
                        dev = args.get("device_id")
                        param = args.get("parameter") or args.get("temperature") or args.get("value")
                        ok, _ = execute_ha_command({
                            "status": "success",
                            "action": action,
                            "device_id": dev,
                            "parameter": param,
                        })
                        if ok:
                            ha_executed = True
                elif model_status == "success" and model_device not in ("null", "multiple"):
                    ok, _ = execute_ha_command({
                        "status": model_status,
                        "action": model_action,
                        "device_id": model_device,
                        "parameter": model_param,
                    })
                    if ok:
                        ha_executed = True

                if ha_executed:
                    validation_result = validate_single_element(
                        item,
                        ha_url=HA_API_URL,
                        headers=ha_validation_headers,
                        cache=ha_validation_cache,
                    )
                    ha_state_valid = validation_result["result"]
                    logger.info("✓ Validación HA: %s", validation_result["message"])
                metrics_snapshot = stop_metrics_monitor(
                    metrics_thread,
                    metrics_stop_event,
                    metrics_by_device_and_second,
                    DEVICE_NAME,
                    baseline_ram_mb=baseline_ram_mb,
                )
                ram_mb = metrics_snapshot["ram_usage_mb"]
                cpu_pct = metrics_snapshot["cpu_usage_pct"]
                gpu_pct = metrics_snapshot["gpu_usage_pct"]
                vram_mb = metrics_snapshot["vram_usage_mb"]
                power_w = metrics_snapshot["power_watts"]
                temp_c = metrics_snapshot["cpu_temp_c"]
                battery_power_watts = metrics_snapshot["battery_power_watts"]

                writer.writerow({
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "hardware_device": DEVICE_NAME,
                    "model_name": MODEL_NAME,
                    "prompt_id": prompt_id,
                    "prompt_type": prompt_type,
                    "latency_sec": latency_sec,
                    "tokens_generated": result["tokens_generated"],
                    "tokens_per_second": result["tokens_per_second"],
                    "ram_baseline_mb": baseline_ram_mb,
                    "ram_usage_mb": ram_mb,
                    "cpu_usage_pct": cpu_pct,
                    "gpu_usage_pct": gpu_pct,
                    "vram_usage_mb": vram_mb,
                    "power_watts": power_w,
                    "cpu_temp_c": temp_c,
                    "battery_power_watts": battery_power_watts,
                    "json_valid": json_valid,
                    "ha_executed": ha_executed,
                    "ha_state_valid": ha_state_valid,
                    "model_reasoning": result["model_reasoning"].replace("\n", " ") if result["model_reasoning"] else "",
                    "raw_response": result["raw_response"].replace("\n", " "),
                })
                csv_file.flush()

                logger.info("✓ Latencia: %ss | Rendimiento: %s tok/s", latency_sec, result["tokens_per_second"])
                logger.debug("✓ CPU: %s%% | RAM: %s MB", cpu_pct, ram_mb)
                logger.debug("✓ GPU: %s%% | VRAM: %s MB", gpu_pct, vram_mb)
                logger.debug("✓ Potencia: %s W | Temp: %s°C", power_w, temp_c)
                logger.debug("✓ Batería: %s W de consumo", battery_power_watts)
                logger.info("✓ Tool Calling válido: %s", json_valid)
                logger.info("✓ Ejecución HA: %s", ha_executed)
                logger.debug("✓ Respuesta: %s", result["raw_response"][:500])
            except Exception as exc:
                logger.error("✗ Error: %s", exc)
                
            time.sleep(2)



if __name__ == "__main__":
    args = parse_args()
    configure_logging(cli_level=args.log_level)
    run_benchmark(model_name=args.model, device_name=args.device_name, prompt_id=args.prompt_id)
