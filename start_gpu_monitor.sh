#!/bin/bash
# start_gpu_monitor.sh
# Launches the live NVIDIA GPU monitor (monitor_gpu.py).
# On classic terminals it runs in the current window; use a terminal with
# split panes (e.g. tmux) if you want it in a separate pane.
#
# Usage:
#   ./start_gpu_monitor.sh
#   ./start_gpu_monitor.sh --refresh 2

set -e
cd "$(dirname "$0")"

if command -v python3 >/dev/null 2>&1; then
    exec python3 -u monitor_gpu.py "$@"
elif command -v python >/dev/null 2>&1; then
    exec python -u monitor_gpu.py "$@"
else
    echo "Python 3 was not found. Install Python 3 and add it to PATH." >&2
    exit 1
fi