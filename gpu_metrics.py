#!/usr/bin/env python3
"""Unified GPU metrics reader for discrete NVIDIA GPUs and NVIDIA Jetson boards.

On discrete NVIDIA GPUs (desktop/server, Linux or Windows) metrics are read
with ``nvidia-smi``. On NVIDIA Jetson boards (Jetson Orin Nano, Orin,
Xavier, Nano, TX…) ``nvidia-smi`` is NOT shipped, so the metrics are read
directly from sysfs, which is the same source ``tegrastats``/``jtop`` use:

* GPU utilization  -> ``/sys/devices/platform/17000000.gpu/load`` on JetPack 6
                       (raw value / 10 = %); on older L4T (JetPack 5 and earlier)
                       ``/sys/devices/gpu.0/load`` (raw value / 10000 = %)
* GPU temperature  -> ``/sys/devices/virtual/thermal/thermal_zone*/type``
                       (the zone whose type matches "GPU", e.g. "GPU-therm")
* GPU core clock   -> ``/sys/class/devfreq/57000000.gpu/cur_freq`` (Hz -> MHz)
* Memory           -> ``/proc/meminfo``  (see the VRAM note below)
* Board model      -> ``/proc/device-tree/model`` (e.g. "NVIDIA Orin Nano
                       Developer Kit")
* SW version       -> ``/etc/nv_tegra_release`` (L4T / JetPack release)

VRAM note
---------
Jetson boards do not have dedicated VRAM: the integrated GPU allocates from
the same LPDDR system memory pool used by the CPU. There is no per-process
GPU memory query without ``nvidia-smi``, so:

* ``mem_used_mb`` / ``mem_total_mb``  are reported from ``/proc/meminfo`` as
  the closest available proxy for the shared GPU/CPU memory pool, and
* the per-process GPU table (``get_gpu_processes``) is unavailable and
  returns an empty list.

Backend selection is automatic: ``nvidia-smi`` first, then Jetson sysfs.
"""

import os
import re
import shutil
import subprocess
import sys

# --- nvidia-smi query (same field order monitor_gpu.py used) ---
GPU_QUERY = ",".join(
    [
        "name",
        "driver_version",
        "utilization.gpu",
        "memory.used",
        "memory.total",
        "temperature.gpu",
        "power.draw",
        "power.limit",
        "fan.speed",
        "clocks.sm",
        "clocks.mem",
    ]
)

# --- Jetson (Tegra) sysfs locations ---
# JetPack 6 (L4T 36.x, Orin boards, Ubuntu 22.04 and later): the iGPU is exposed
# as a platform device and its load file already uses a /10 scale
# (e.g. 450 -> 45.0%).
GPU_LOAD_PATH = "/sys/devices/platform/17000000.gpu/load"
GPU_LOAD_SCALE = 10.0
# Older L4T (JetPack 5 and earlier): /sys/devices/gpu.0/load scaled by 10000
# (e.g. 375000 -> 37.5%). Kept as a fallback when the platform node is absent.
GPU_LOAD_PATH_LEGACY = "/sys/devices/gpu.0/load"
GPU_LOAD_SCALE_LEGACY = 10000.0
THERMAL_DIR = "/sys/devices/virtual/thermal"
DEVFREQ_DIR = "/sys/class/devfreq"
IGPU_FREQ_NODE = "57000000.gpu"
MEMINFO_PATH = "/proc/meminfo"
MODEL_PATH = "/proc/device-tree/model"
L4T_RELEASE_PATH = "/etc/nv_tegra_release"

# Values nvidia-smi prints when a metric is not supported.
_UNAVAILABLE = ("N/A", "[N/A]", "[NOT SUPPORTED]", "NOT SUPPORTED")


# --- low level readers ---
def _read_text(path):
    """Return the stripped contents of a small file, or None."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except (OSError, UnicodeDecodeError):
        return None


def _read_int(path):
    text = _read_text(path)
    if text is None:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _read_float(path):
    text = _read_text(path)
    if text is None:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _to_float(value):
    """Parse a value that may be 'N/A', '[N/A]'… returning float or None."""
    text = (value or "").strip()
    if not text or text.upper() in _UNAVAILABLE:
        return None
    try:
        return float(text)
    except ValueError:
        return None


# --- backend detection ---
def nvidia_smi_available():
    """True when an nvidia-smi binary exists in PATH (Linux or Windows)."""
    if shutil.which("nvidia-smi"):
        return True
    if os.name == "nt" and shutil.which("nvidia-smi.exe"):
        return True
    return False


def is_jetson():
    """Detect an NVIDIA Jetson (Tegra) board from filesystem markers."""
    if not sys.platform.startswith("linux"):
        return False
    if os.path.exists(GPU_LOAD_PATH) or os.path.exists(GPU_LOAD_PATH_LEGACY):
        return True
    if os.path.exists(L4T_RELEASE_PATH):
        return True
    if shutil.which("tegrastats"):
        return True
    model = _read_text(MODEL_PATH)
    if model and re.search(r"(jetson|orin|xavier|nano|tegra|tx\d)", model, re.IGNORECASE):
        return True
    return False


def get_backend_name():
    """Return the active collector backend: 'nvidia-smi', 'jetson' or None."""
    if nvidia_smi_available():
        return "nvidia-smi"
    if is_jetson():
        return "jetson"
    return None
# --- nvidia-smi backend ---
def _run_nvidia_smi(query, query_flag="--query-gpu", extra_flags=None):
    """Run nvidia-smi for the given query and return stdout, or None."""
    cmd = ["nvidia-smi", f"{query_flag}={query}", "--format=csv,noheader,nounits"]
    if extra_flags:
        cmd.extend(extra_flags)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return result.stdout


def get_gpu_metrics_nvidia_smi():
    """Return the metrics dict of the first NVIDIA GPU, or None."""
    raw = _run_nvidia_smi(GPU_QUERY)
    if not raw:
        return None
    first_line = next((line.strip() for line in raw.splitlines() if line.strip()), "")
    parts = [p.strip() for p in first_line.split(",")]
    if len(parts) < 11:
        return None
    return {
        "backend": "nvidia-smi",
        "name": parts[0] or "NVIDIA GPU",
        "driver_version": parts[1],
        "util_gpu": _to_float(parts[2]),
        "mem_used_mb": _to_float(parts[3]),
        "mem_total_mb": _to_float(parts[4]),
        "temp_gpu": _to_float(parts[5]),
        "power_draw_w": _to_float(parts[6]),
        "power_limit_w": _to_float(parts[7]),
        "fan_speed_pct": _to_float(parts[8]),
        "clk_sm_mhz": _to_float(parts[9]),
        "clk_mem_mhz": _to_float(parts[10]),
    }


# --- Jetson (Tegra) sysfs backend ---
def _read_meminfo_mb():
    """Return (total_mb, used_mb) from /proc/meminfo, or (None, None).

    Equivalent to psutil.virtual_memory() on Linux: ``total`` maps to
    ``MemTotal`` and ``available`` to ``MemAvailable`` (the same fields psutil
    reports as ``.total`` and ``.available``); ``used`` = MemTotal - MemAvailable.
    The Jetson iGPU shares the system LPDDR pool, so this is the closest
    available proxy for GPU memory usage without nvidia-smi.
    """
    text = _read_text(MEMINFO_PATH)
    if text is None:
        return None, None
    total_kb = None
    available_kb = 0
    for line in text.splitlines():
        match = re.match(r"MemTotal:\s+(\d+)\s+kB", line)
        if match:
            total_kb = int(match.group(1))
            continue
        match = re.match(r"MemAvailable:\s+(\d+)\s+kB", line)
        if match:
            available_kb = int(match.group(1))
    if total_kb is None:
        return None, None
    used_kb = max(0, total_kb - available_kb)
    return round(total_kb / 1024.0, 1), round(used_kb / 1024.0, 1)


def _read_gpu_temperature():
    """Return the GPU temperature in Celsius reading the thermal zones."""
    if not os.path.isdir(THERMAL_DIR):
        return None
    try:
        zones = sorted(os.listdir(THERMAL_DIR))
    except OSError:
        return None
    gpu_zone = None
    fallback_zone = None
    for zone in zones:
        if not zone.startswith("thermal_zone"):
            continue
        zone_dir = os.path.join(THERMAL_DIR, zone)
        zone_type = _read_text(os.path.join(zone_dir, "type"))
        if not zone_type:
            continue
        if re.search(r"GPU", zone_type, re.IGNORECASE):
            gpu_zone = zone_dir
            break
        if fallback_zone is None:
            fallback_zone = zone_dir
    zone_dir = gpu_zone or fallback_zone
    if zone_dir is None:
        return None
    temp = _read_float(os.path.join(zone_dir, "temp"))
    if temp is None:
        return None
    return round(temp / 1000.0, 1)
def _find_gpu_devfreq_dir():
    """Return the devfreq dir for the iGPU, or None."""
    node = os.path.join(DEVFREQ_DIR, IGPU_FREQ_NODE)
    if os.path.isdir(node):
        return node
    try:
        names = sorted(os.listdir(DEVFREQ_DIR)) if os.path.isdir(DEVFREQ_DIR) else []
    except OSError:
        return None
    for name in names:
        if name.endswith(".gpu"):
            return os.path.join(DEVFREQ_DIR, name)
    return None


def _read_gpu_clocks():
    """Return (sm_mhz, mem_mhz) for the iGPU; mem clock is not exposed.

    devfreq ``cur_freq`` is reported in Hz (e.g. 1300500000 Hz = 1300.5 MHz),
    while nvidia-smi reports clocks.sm already in MHz; convert here to MHz.
    """
    freq_dir = _find_gpu_devfreq_dir()
    if freq_dir is None:
        return None, None
    cur = _read_int(os.path.join(freq_dir, "cur_freq"))
    sm_mhz = round(cur / 1000000.0) if cur is not None else None
    return sm_mhz, None


def _read_l4t_version():
    """Return the L4T/JetPack version string, or None."""
    text = _read_text(L4T_RELEASE_PATH)
    if not text:
        return None
    match = re.search(r"R(\d+) \(release\), REVISION: ([\d.]+)", text)
    if match:
        return f"L4T {match.group(1)}.{match.group(2)}"
    return "L4T"


def _read_gpu_load_pct():
    """Return the GPU utilization percentage (0-100) from the Jetson load file.

    JetPack 6 (L4T 36.x) on Jetson Orin exposes the iGPU load at
    ``/sys/devices/platform/17000000.gpu/load`` with a /10 scale
    (e.g. 450 -> 45.0%). Older L4T (JetPack 5 and earlier) used
    ``/sys/devices/gpu.0/load`` with a /10000 scale (e.g. 375000 -> 37.5%).
    Returns None when neither file is available.
    """
    for path, scale in (
        (GPU_LOAD_PATH, GPU_LOAD_SCALE),
        (GPU_LOAD_PATH_LEGACY, GPU_LOAD_SCALE_LEGACY),
    ):
        raw = _read_int(path)
        if raw is not None:
            return max(0.0, min(100.0, raw / scale))
    return None


def get_gpu_metrics_jetson():
    """Return the metrics dict read from Jetson sysfs, or None."""
    if not is_jetson():
        return None

    util_gpu = _read_gpu_load_pct()

    mem_total_mb, mem_used_mb = _read_meminfo_mb()
    sm_mhz, mem_clk_mhz = _read_gpu_clocks()

    return {
        "backend": "jetson",
        "name": _read_text(MODEL_PATH) or "NVIDIA Jetson",
        "driver_version": _read_l4t_version(),
        "util_gpu": util_gpu,
        "mem_used_mb": mem_used_mb,
        "mem_total_mb": mem_total_mb,
        "temp_gpu": _read_gpu_temperature(),
        # Not exposed on Jetson without nvidia-smi.
        "power_draw_w": None,
        "power_limit_w": None,
        "fan_speed_pct": None,
        "clk_sm_mhz": sm_mhz,
        "clk_mem_mhz": mem_clk_mhz,
    }


# --- public API ---
def get_gpu_metrics():
    """Return a unified metrics dict from the first working backend, or None.

    The dict uses the same keys as the old monitor_gpu.py query:
    name, driver_version, util_gpu, mem_used_mb, mem_total_mb, temp_gpu,
    power_draw_w, power_limit_w, fan_speed_pct, clk_sm_mhz, clk_mem_mhz,
    backend.
    """
    if nvidia_smi_available():
        metrics = get_gpu_metrics_nvidia_smi()
        if metrics is not None:
            return metrics
    # Some JetPack builds ship a limited nvidia-smi that fails at runtime;
    # fall back to sysfs whenever the board looks like a Jetson.
    if is_jetson():
        metrics = get_gpu_metrics_jetson()
        if metrics is not None:
            return metrics
    return None


def get_gpu_sample():
    """Return {util, mem_used, mem_total, temp} or None (any backend).

    Convenience wrapper used by the lightweight monitor (monitor_gpu_lite.py).
    """
    metrics = get_gpu_metrics()
    if metrics is None:
        return None
    return {
        "util": metrics.get("util_gpu"),
        "mem_used": metrics.get("mem_used_mb"),
        "mem_total": metrics.get("mem_total_mb"),
        "temp": metrics.get("temp_gpu"),
    }


def get_gpu_processes():
    """Return a list of {pid, name, mem_mb} for GPU compute apps.

    Only available with nvidia-smi (Jetson returns an empty list).
    """
    if not nvidia_smi_available():
        return []
    raw = _run_nvidia_smi("pid,process_name,used_memory", query_flag="--query-compute-apps")
    if not raw:
        return []
    processes = []
    for line in raw.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            continue
        pid = parts[0]
        # process_name may itself contain commas; only the last field is memory.
        name = os.path.basename(",".join(parts[1:-1]).strip()) or "unknown"
        processes.append({"pid": pid, "name": name, "mem_mb": _to_float(parts[-1])})
    return processes