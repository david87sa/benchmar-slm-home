# Benchmark Lab Project

## Overview
This project benchmarks Home Assistant function-calling workflows using Ollama models and automated validation. It sends prompts to an Ollama model, parses tool/function calls, executes corresponding Home Assistant actions, and validates the resulting entity state.

## Main Components

### 1. Benchmark script
- File: [benchmark.py](benchmark.py)
- Runs the end-to-end benchmark loop.
- Supports:
  - model selection via CLI argument or environment variable
  - device-name selection via CLI argument or auto-detection
  - configuration loading from [data/config.json](data/config.json)
  - Home Assistant execution and validation
  - configurable logging via --log-level

### 2. Home Assistant validation helper
- File: [check_ha_states.py](check_ha_states.py)
- Queries Home Assistant entity states.
- Validates whether the live state matches the expected state from the benchmark prompts.
- Uses authentication headers for Home Assistant requests.

### 3. Device info helper
- File: [device_info.py](device_info.py)
- Detects a hardware-style device name for the benchmark results.
- Falls back to the OS hostname if no hardware model can be determined.

### 4. Configuration
- File: [data/config.json](data/config.json)
- Stores configurable values such as:
  - model name
  - MQTT broker address
  - MQTT topic for power data
  - Home Assistant API key
  - log level (see Logging below)

### 5. Logging
- File: [logger.py](logger.py)
- Centralized logging configuration supporting INFO and DEBUG levels (plus WARNING, ERROR, CRITICAL).
- Log level can be configured via (highest priority first):
  1. CLI argument --log-level (available on benchmark.py, check_ha_states.py, monitor_gpu.py, and server/run_demo.py)
  2. Environment variable LOG_LEVEL
  3. data/config.json log_level key
  4. Default: INFO
- Log output goes to stderr (keeping stdout clean for structured/JSON output).
- Format: YYYY-MM-DD HH:MM:SS [LEVEL] [module] message

### 6. Data files
- Directory: [data](data)
- Contains:
  - test prompts
  - system prompt
  - tool definitions
  - Home Assistant token file

### 7. Demo/server assets
- Directory: [server](server)
- Includes demo setup scripts and Home Assistant configuration examples.

### 8. GPU monitor
- File: [monitor_gpu.py](monitor_gpu.py)
- Live, auto-refreshing terminal dashboard (like `nvidia-smi`/`nvtop`):
  - GPU model and driver version
  - GPU utilization and VRAM usage with progress bars
  - Temperature, power draw, fan speed and SM/memory clocks
  - The compute processes currently using the GPU
- Run it directly:

      python monitor_gpu.py

- Or open it in a **separate console window** with the launchers:
  - Windows: `powershell -ExecutionPolicy Bypass -File .\start_gpu_monitor.ps1`
  - Linux/macOS: `./start_gpu_monitor.sh`
- Options:
  - `--refresh N`  refresh interval in seconds (default: 1.0)
  - `--as-log`      single log line per sample (old style, useful when piping)
  - `--log-level LEVEL`  logging level (same resolution order as elsewhere)
- When stdout is piped or redirected, the script automatically falls back to the log-line mode.

### 9. Lightweight GPU usage logger
- File: [monitor_gpu_lite.py](monitor_gpu_lite.py)
- Minimal, dependency-free script that prints one line per sample with
  **millisecond-precision timestamps** and GPU usage, handy for correlating
  GPU state with benchmark events or piping to a log file:

      python monitor_gpu_lite.py --refresh 0.2

  ```
  2026-08-10 21:05:10.745 | GPU   0.0% | VRAM 0/8151 MiB
  ```
- Options: `--refresh N` (seconds between samples), `--no-vram` (utilization only), `--show-temp` (also print temperature).

## Workflow
1. The benchmark script loads configuration from [data/config.json](data/config.json).
2. It sends a prompt to Ollama with the configured model.
3. It parses the model response for tool/function calls.
4. It executes the corresponding Home Assistant action.
5. It checks the resulting Home Assistant entity state.
6. It writes results to [benchmark_results_master.csv](benchmark_results_master.csv).

## Usage
Run the benchmark with:

    python benchmark.py --model qwen2.5:3b

Or with a custom device label:

    python benchmark.py --model qwen2.5:3b --device-name my-laptop

Enable debug logging to see detailed execution information:

    python benchmark.py --model qwen2.5:3b --log-level debug

## Notes
- The project expects a running Ollama instance and a reachable Home Assistant instance.
- MQTT support is used for collecting power data during benchmarking.
- Authentication for Home Assistant can come from the configured API key or a token file.
- Logging can be tuned via --log-level, LOG_LEVEL env var, or config.json.
