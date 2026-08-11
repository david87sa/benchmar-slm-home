#!/usr/bin/env python3
"""Continuously print the current time (with milliseconds) and GPU usage.

A lightweight companion to monitor_gpu.py: one line per sample with
millisecond precision, handy for correlating GPU state with benchmark events
or for piping into a log file. Uses only the standard library.

Usage:
    python monitor_gpu_lite.py                  # 1 sample/second
    python monitor_gpu_lite.py --refresh 0.1    # ~10 samples/second
    python monitor_gpu_lite.py --no-vram        # utilization only
    python monitor_gpu_lite.py --show-temp      # also print temperature

Example output:
    2026-08-10 21:05:32.123 | GPU  0.0% | VRAM 0/8151 MiB
"""

import argparse
import subprocess
import sys
import time
from datetime import datetime

GPU_QUERY = "utilization.gpu,memory.used,memory.total,temperature.gpu"


def now_ms():
    """Current local time with millisecond precision, e.g. 2026-08-10 21:05:32.123."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def get_gpu_sample():
    """Query nvidia-smi once and return {util, mem_used, mem_total, temp} or None."""
    try:
        result = subprocess.run(
            ["nvidia-smi", f"--query-gpu={GPU_QUERY}", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None

    parts = [p.strip() for p in result.stdout.strip().splitlines()[0].split(",")]
    try:
        return {
            "util": float(parts[0]),
            "mem_used": float(parts[1]),
            "mem_total": float(parts[2]),
            "temp": float(parts[3]),
        }
    except (ValueError, IndexError):
        return None


def print_line(sample, args):
    fields = [now_ms()]
    if sample is None:
        fields.append("GPU N/A")
    else:
        fields.append(f"GPU {sample['util']:5.1f}%")
        if not args.no_vram:
            fields.append(f"VRAM {sample['mem_used']:.0f}/{sample['mem_total']:.0f} MiB")
        if args.show_temp:
            fields.append(f"Temp {sample['temp']:.0f}°C")
    print(" | ".join(fields), flush=True)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Print millisecond timestamp and GPU usage continuously"
    )
    parser.add_argument(
        "--refresh",
        "-r",
        type=float,
        default=1.0,
        help="Seconds between samples (default: 1.0; use 0.1 for fast sampling)",
    )
    parser.add_argument(
        "--no-vram",
        action="store_true",
        help="Print GPU utilization only (no VRAM column)",
    )
    parser.add_argument(
        "--show-temp",
        action="store_true",
        help="Also print GPU temperature",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    try:
        while True:
            print_line(get_gpu_sample(), args)
            time.sleep(args.refresh)
    except KeyboardInterrupt:
        print("\nStopped GPU monitor.", file=sys.stderr)


if __name__ == "__main__":
    main()