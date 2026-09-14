#!/usr/bin/env python3
"""Live NVIDIA GPU monitor for the terminal (nvidia-smi / nvtop style).

Renders an auto-refreshing dashboard in its own console window showing:
  - GPU model and driver version
  - GPU utilization and VRAM usage (with progress bars)
  - Temperature, power draw, fan speed and SM/memory clocks
  - The compute processes currently using the GPU

Usage:
    python monitor_gpu.py                # live dashboard, refresh every 1 s
    python monitor_gpu.py --refresh 2    # slower refresh
    python monitor_gpu.py --as-log       # one log line per sample (old style)

Launchers (open the monitor in a new, separate console window):
    start_gpu_monitor.ps1   (Windows)
    start_gpu_monitor.sh    (Linux/macOS)

When stdout is not an interactive terminal (e.g. piped or redirected output)
the script automatically falls back to the one-log-line-per-sample mode.
"""

import argparse
import os
import shutil
import sys
import time

import gpu_metrics
from logger import configure_logging, get_logger

logger = get_logger(__name__)

# ANSI escape codes
_RST = "\x1b[0m"
_BOLD = "\x1b[1m"
_DIM = "\x1b[2m"
_RED = "\x1b[31m"
_GREEN = "\x1b[32m"
_YELLOW = "\x1b[33m"
_CYAN = "\x1b[36m"


# --- GPU readers (shared with benchmark.py / monitor_gpu_lite.py) ---
# gpu_metrics reads nvidia-smi on discrete NVIDIA GPUs and falls back to
# sysfs on Jetson boards (Orin Nano, Xavier, Nano, TX…) where nvidia-smi
# is not available.
get_gpu_metrics = gpu_metrics.get_gpu_metrics
get_gpu_processes = gpu_metrics.get_gpu_processes


# --- console helpers ---
def _setup_console():
    """Enable ANSI VT processing and set a window title (Windows consoles)."""
    if os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
            kernel32.SetConsoleTitleW("GPU Monitor")
        except Exception:
            pass


def _terminal_width():
    try:
        return max(shutil.get_terminal_size((80, 24)).columns, 40)
    except Exception:
        return 80


def _bar(fraction, width):
    width = max(width, 1)
    filled = int(round(max(0.0, min(1.0, fraction)) * width))
    return "█" * filled + "░" * (width - filled)


def _fmt_mib(value):
    if value is None:
        return "N/A"
    if value >= 1024:
        return f"{value / 1024:.1f} GiB"
    return f"{value:.0f} MiB"


def _util_color(value):
    if value is None:
        return _DIM
    if value >= 80:
        return _RED
    if value >= 40:
        return _YELLOW
    return _GREEN


def _temp_color(value):
    if value is None:
        return _DIM
    if value >= 85:
        return _RED
    if value >= 65:
        return _YELLOW
    return _GREEN


def _render(metrics, processes, refresh):
    """Build the dashboard text for one frame (no ANSI clear codes)."""
    width = _terminal_width()
    bar_width = max(width - 34, 10)
    rule = "─" * width

    lines = [
        f"{_BOLD}{_CYAN}GPU MONITOR{_RST}  {time.strftime('%Y-%m-%d %H:%M:%S')}   "
        f"({_DIM}Ctrl+C to stop | refresh {refresh:g}s{_RST})"
    ]

    if metrics is None:
        lines.append(f"{_RED}GPU unavailable.{_RST}")
        lines.append("  No NVIDIA GPU detected (nvidia-smi not found and no Jetson iGPU sysfs).")
    else:
        name = metrics["name"]
        driver = metrics["driver_version"] or "?"
        lines.append(f"{_BOLD}{name}{_RST}   {_DIM}Driver {driver}{_RST}")
        lines.append(rule)

        util = metrics["util_gpu"]
        util_str = "N/A" if util is None else f"{util:5.1f}"
        util_color = _util_color(util)
        lines.append(
            f"  Utilization  {util_color}{util_str}%{_RST}  "
            f"{util_color}{_bar((util or 0) / 100, bar_width)}{_RST}"
        )

        mem_used = metrics["mem_used_mb"]
        mem_total = metrics["mem_total_mb"]
        if mem_used is not None and mem_total:
            mem_frac = mem_used / mem_total
            mem_pct = mem_frac * 100
            mem_label = f"{_fmt_mib(mem_used)} / {_fmt_mib(mem_total)} ({mem_pct:3.0f}%)"
        else:
            mem_frac, mem_pct, mem_label = 0.0, None, "N/A"
        mem_color = _util_color(mem_pct)
        lines.append(
            f"  Memory       {mem_color}{mem_label:>30}{_RST}  "
            f"{mem_color}{_bar(mem_frac, bar_width)}{_RST}"
        )

        temp = metrics["temp_gpu"]
        temp_str = "N/A" if temp is None else f"{temp:.0f}°C"

        power = metrics["power_draw_w"]
        if power is None:
            power_str = "N/A"
        else:
            power_str = f"{power:.1f} W"
            if metrics["power_limit_w"]:
                power_str += f" / {metrics['power_limit_w']:.0f} W"

        fan = metrics["fan_speed_pct"]
        fan_str = "N/A" if fan is None else f"{fan:.0f}%"

        lines.append(
            f"  Temperature  {_temp_color(temp)}{temp_str}{_RST}"
            f"   Power  {power_str}   Fan  {fan_str}"
        )

        sm_clk = metrics["clk_sm_mhz"]
        mem_clk = metrics["clk_mem_mhz"]
        sm_str = "N/A" if sm_clk is None else f"{sm_clk:.0f} MHz"
        mem_str = "N/A" if mem_clk is None else f"{mem_clk:.0f} MHz"
        lines.append(f"  Clocks       SM {sm_str:>10}   Memory {mem_str}")

    lines.append(rule)
    lines.append(f"  {_BOLD}Processes ({len(processes)}){_RST}")
    if not processes:
        lines.append(f"  {_DIM}(nothing using the GPU){_RST}")
    else:
        for proc in processes[:10]:
            lines.append(
                f"    PID {proc['pid']:<7} {proc['name'][:40]:<40} {_fmt_mib(proc['mem_mb'])}"
            )
        if len(processes) > 10:

            lines.append(f"  {_DIM}… and {len(processes) - 10} more{_RST}")
    lines.append(rule)

    return "\n".join(lines)


# --- run modes ---
def run_dashboard(args):
    _setup_console()
    logger.info("Starting GPU dashboard. Ctrl+C to stop.")
    try:
        while True:
            metrics = get_gpu_metrics()
            processes = get_gpu_processes()
            frame = "\x1b[2J\x1b[H" + _render(metrics, processes, args.refresh)
            try:
                print(frame, end="", flush=True)
            except BrokenPipeError:
                sys.exit(0)
            time.sleep(args.refresh)
    except KeyboardInterrupt:
        print(f"\n{_GREEN}Stopped GPU monitor.{_RST}")


def run_log_mode(args):
    logger.info("Monitoring NVIDIA GPU usage. Press Ctrl+C to stop.")
    try:
        while True:
            metrics = get_gpu_metrics()
            if metrics is None:
                logger.warning("GPU unavailable")
            else:
                logger.info(
                    "GPU Utilization: %.1f%% | VRAM: %.1f/%.1f MB",
                    metrics["util_gpu"] or 0.0,
                    metrics["mem_used_mb"] or 0.0,
                    metrics["mem_total_mb"] or 0.0,
                )
            time.sleep(args.refresh)
    except KeyboardInterrupt:
        logger.info("Stopped GPU monitor.")


# --- CLI ---
def parse_args():
    parser = argparse.ArgumentParser(
        description="Monitor NVIDIA GPU utilization, VRAM, temperature and power"
    )
    parser.add_argument(
        "--refresh",
        "-r",
        type=float,
        default=1.0,
        help="Refresh interval in seconds (default: 1.0)",
    )
    parser.add_argument(
        "--as-log",
        action="store_true",
        help="Print one log line per sample instead of the live dashboard",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["debug", "info", "warning", "error", "critical"],
        help="Logging level (default: env LOG_LEVEL, config.json, or INFO)",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    configure_logging(cli_level=args.log_level)

    if args.as_log or not sys.stdout.isatty():
        run_log_mode(args)
    else:
        run_dashboard(args)


if __name__ == "__main__":
    main()
