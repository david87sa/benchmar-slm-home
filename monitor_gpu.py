#!/usr/bin/env python3
"""Monitor NVIDIA GPU utilization and VRAM usage in the terminal."""

import subprocess
import sys
import time


def get_gpu_metrics():
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode != 0:
            return None

        line = result.stdout.strip().splitlines()[0]
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            return None

        return {
            "gpu_util_pct": float(parts[0]),
            "vram_used_mb": float(parts[1]),
            "vram_total_mb": float(parts[2]),
        }
    except Exception:
        return None


def main():
    print("Monitoring NVIDIA GPU usage. Press Ctrl+C to stop.")
    try:
        while True:
            metrics = get_gpu_metrics()
            if metrics is None:
                print("[GPU] unavailable")
            else:
                print(
                    f"[GPU] Utilization: {metrics['gpu_util_pct']:.1f}% | "
                    f"VRAM: {metrics['vram_used_mb']:.1f}/{metrics['vram_total_mb']:.1f} MB"
                )
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopped GPU monitor.")


if __name__ == "__main__":
    main()
