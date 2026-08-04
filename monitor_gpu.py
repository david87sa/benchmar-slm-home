#!/usr/bin/env python3
"""Monitor NVIDIA GPU utilization and VRAM usage in the terminal."""

import argparse
import subprocess
import sys
import time

from logger import configure_logging, get_logger

logger = get_logger(__name__)


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


def parse_args():
    parser = argparse.ArgumentParser(description="Monitor NVIDIA GPU utilization and VRAM usage")
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

    logger.info("Monitoring NVIDIA GPU usage. Press Ctrl+C to stop.")
    try:
        while True:
            metrics = get_gpu_metrics()
            if metrics is None:
                logger.warning("GPU unavailable")
            else:
                logger.info(
                    "GPU Utilization: %.1f%% | VRAM: %.1f/%.1f MB",
                    metrics["gpu_util_pct"],
                    metrics["vram_used_mb"],
                    metrics["vram_total_mb"],
                )
            time.sleep(0.5)
    except KeyboardInterrupt:
        logger.info("Stopped GPU monitor.")


if __name__ == "__main__":
    main()
