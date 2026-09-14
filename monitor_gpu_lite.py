#!/usr/bin/env python3
"""Continuously print the current time (with milliseconds) and GPU usage.

A lightweight companion to monitor_gpu.py: one line per sample with
millisecond precision, handy for correlating GPU state with benchmark events
or for piping into a log file. Uses only the standard library.

Works on discrete NVIDIA GPUs (via nvidia-smi) and on NVIDIA Jetson boards
(Orin Nano, etc.) via the shared gpu_metrics reader, which falls back to
sysfs when nvidia-smi is not available.

Usage:
    python monitor_gpu_lite.py                  # 1 sample/second
    python monitor_gpu_lite.py --refresh 0.1    # ~10 samples/second
    python monitor_gpu_lite.py --no-vram        # utilization only
    python monitor_gpu_lite.py --show-temp      # also print temperature

Example output:
    2026-08-10 21:05:32.123 | GPU  0.0% | VRAM 0/8151 MiB
"""

import argparse
import sys
import time
from datetime import datetime

import gpu_metrics


def now_ms():
    """Current local time with millisecond precision, e.g. 2026-08-10 21:05:32.123."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def get_gpu_sample():
    """Sample GPU state once and return {util, mem_used, mem_total, temp} or None."""
    return gpu_metrics.get_gpu_sample()


def print_line(sample, args):
    fields = [now_ms()]
    if sample is None or sample.get("util") is None:
        fields.append("GPU N/A")
    else:
        fields.append(f"GPU {sample['util']:5.1f}%")
        if not args.no_vram:
            if sample.get("mem_used") is not None and sample.get("mem_total") is not None:
                fields.append(f"VRAM {sample['mem_used']:.0f}/{sample['mem_total']:.0f} MiB")
            else:
                fields.append("VRAM N/A")
        if args.show_temp:
            if sample.get("temp") is not None:
                fields.append(f"Temp {sample['temp']:.0f}°C")
            else:
                fields.append("Temp N/A")
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