# Benchmark Lab Project

## Overview
This project benchmarks Home Assistant function-calling workflows using Ollama models and automated validation. It sends prompts to an Ollama model, parses tool/function calls, executes corresponding Home Assistant actions, and validates the resulting entity state.

## Main Components

### 1. Benchmark script
- File: [bench-function-calling.py](bench-function-calling.py)
- Runs the end-to-end benchmark loop.
- Supports:
  - model selection via CLI argument or environment variable
  - device-name selection via CLI argument or auto-detection
  - configuration loading from [data/config.json](data/config.json)
  - Home Assistant execution and validation

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

### 5. Data files
- Directory: [data](data)
- Contains:
  - test prompts
  - system prompt
  - tool definitions
  - Home Assistant token file

### 6. Demo/server assets
- Directory: [server](server)
- Includes demo setup scripts and Home Assistant configuration examples.

## Workflow
1. The benchmark script loads configuration from [data/config.json](data/config.json).
2. It sends a prompt to Ollama with the configured model.
3. It parses the model response for tool/function calls.
4. It executes the corresponding Home Assistant action.
5. It checks the resulting Home Assistant entity state.
6. It writes results to [benchmark_results_master.csv](benchmark_results_master.csv).

## Usage
Run the benchmark with:

```bash
python bench-function-calling.py --model qwen2.5:3b
```

Or with a custom device label:

```bash
python bench-function-calling.py --model qwen2.5:3b --device-name my-laptop
```

## Notes
- The project expects a running Ollama instance and a reachable Home Assistant instance.
- MQTT support is used for collecting power data during benchmarking.
- Authentication for Home Assistant can come from the configured API key or a token file.
