import importlib.util
import os
import time
import types
import unittest
from pathlib import Path


class MetricsMonitorTests(unittest.TestCase):
    def setUp(self):
        repo_root = Path(__file__).resolve().parents[1]
        module_path = repo_root / "benchmark.py"
        spec = importlib.util.spec_from_file_location("benchmark", module_path)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_capture_peak_metrics_returns_highest_values(self):
        self.module.current_power_watts = 123.4
        self.module.get_cpu_temperature = lambda: 70.0
        self.module.get_gpu_metrics = lambda: (90.0, 2048.0)
        self.module.psutil.virtual_memory = lambda: types.SimpleNamespace(used=2 * 1024 * 1024 * 1024)
        self.module.psutil.cpu_percent = lambda interval=None: 65.0

        thread, stop_event, metrics_by_device_and_second = self.module.start_metrics_monitor(
            device_name="device-a",
            sample_interval=0.01,
        )
        time.sleep(0.03)
        metrics = self.module.stop_metrics_monitor(
            thread,
            stop_event,
            metrics_by_device_and_second,
            "device-a",
            baseline_ram_mb=1024.0,
        )

        self.assertEqual(metrics["device_name"], "device-a")
        self.assertEqual(metrics["cpu_usage_pct"], 65.0)
        self.assertEqual(metrics["ram_usage_mb"], 1024.0)
        self.assertEqual(metrics["gpu_usage_pct"], 90.0)
        self.assertEqual(metrics["vram_usage_mb"], 2048.0)
        self.assertEqual(metrics["power_watts"], 123.4)
        self.assertEqual(metrics["cpu_temp_c"], 70.0)

    def test_stop_metrics_monitor_uses_ram_delta_from_baseline(self):
        self.module.current_power_watts = 0.0
        self.module.get_cpu_temperature = lambda: 40.0
        self.module.get_gpu_metrics = lambda: (10.0, 512.0)
        self.module.psutil.virtual_memory = lambda: types.SimpleNamespace(used=3 * 1024 * 1024 * 1024)
        self.module.psutil.cpu_percent = lambda interval=None: 20.0

        thread, stop_event, metrics_by_device_and_second = self.module.start_metrics_monitor(
            device_name="device-b",
            sample_interval=0.01,
        )
        time.sleep(0.03)
        metrics = self.module.stop_metrics_monitor(
            thread,
            stop_event,
            metrics_by_device_and_second,
            "device-b",
            baseline_ram_mb=2 * 1024.0,
        )

        self.assertEqual(metrics["ram_usage_mb"], 1024.0)

    def test_parse_tool_calls_accepts_json_string_arguments(self):
        message_obj = {
            "tool_calls": [
                {
                    "function": {
                        "name": "turn_on_device",
                        "arguments": '{"device_id": "input_boolean.kitchen_light"}',
                    }
                }
            ]
        }

        is_valid, device_id, action, parameter, status, tool_calls = self.module.parse_tool_calls(message_obj)

        self.assertTrue(is_valid)
        self.assertEqual(device_id, "input_boolean.kitchen_light")
        self.assertEqual(action, "turn_on")
        self.assertEqual(parameter, None)
        self.assertEqual(status, "success")
        self.assertEqual(len(tool_calls), 1)

    def test_parse_tool_calls_accepts_plain_json_content(self):
        message_obj = {
            "content": '{"status": "success", "action": "turn_off", "device_id": "input_boolean.bedroom_light", "parameter": null}'
        }

        is_valid, device_id, action, parameter, status, tool_calls = self.module.parse_tool_calls(message_obj)

        self.assertTrue(is_valid)
        self.assertEqual(device_id, "input_boolean.bedroom_light")
        self.assertEqual(action, "turn_off")
        self.assertEqual(parameter, None)
        self.assertEqual(status, "success")
        self.assertEqual(tool_calls, [])

    def test_parse_tool_calls_accepts_function_call_string(self):
        message_obj = {
            "content": 'turn_on_device({"device_id": "input_boolean.kitchen_light"})'
        }

        is_valid, device_id, action, parameter, status, tool_calls = self.module.parse_tool_calls(message_obj)

        self.assertTrue(is_valid)
        self.assertEqual(device_id, "input_boolean.kitchen_light")
        self.assertEqual(action, "turn_on")
        self.assertEqual(parameter, None)
        self.assertEqual(status, "success")
        self.assertEqual(tool_calls, [])

    def test_parse_tool_calls_maps_indeterminado_to_status(self):
        message_obj = {"content": "indeterminado"}

        is_valid, device_id, action, parameter, status, tool_calls = self.module.parse_tool_calls(message_obj)

        self.assertTrue(is_valid)
        self.assertEqual(device_id, "null")
        self.assertEqual(action, "null")
        self.assertEqual(parameter, None)
        self.assertEqual(status, "indeterminado")
        self.assertEqual(tool_calls, [])

    def test_parse_tool_calls_maps_fallo_to_indeterminado(self):
        message_obj = {"content": "fallo"}

        is_valid, device_id, action, parameter, status, tool_calls = self.module.parse_tool_calls(message_obj)

        self.assertTrue(is_valid)
        self.assertEqual(status, "indeterminado")
        self.assertEqual(tool_calls, [])

    def test_execute_ha_command_uses_configured_token(self):
        captured = {}

        def fake_post(url, headers=None, json=None, timeout=5):
            captured["headers"] = headers
            return types.SimpleNamespace(status_code=200)

        self.module.requests.post = fake_post

        ok, message = self.module.execute_ha_command({
            "status": "success",
            "action": "turn_on",
            "device_id": "input_boolean.kitchen_light",
            "parameter": None,
        })

        self.assertTrue(ok)
        self.assertEqual(captured["headers"]["Authorization"], f"Bearer {self.module.HA_API_PASSWORD}")
        self.assertEqual(message, "Ejecutado con éxito")


if __name__ == "__main__":
    unittest.main()