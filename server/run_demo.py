#!/usr/bin/env python3
import os
import sys
import subprocess
import time
import shutil
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).parent.resolve()
CONFIG_DIR = BASE_DIR / "config"
PID_FILE = BASE_DIR / "hass.pid"

# Docker Settings
CONTAINER_NAME = "homeassistant-demo"
IMAGE_NAME = "ghcr.io/home-assistant/home-assistant:stable"

def print_banner():
    banner = """
===========================================================
    Home Assistant Demo Instance Manager (OS-Agnostic)
===========================================================
    """
    print(banner)

def is_docker_running():
    try:
        # Run docker info to see if docker daemon is running
        res = subprocess.run(["docker", "info"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=3)
        return res.returncode == 0
    except (subprocess.SubprocessError, FileNotFoundError):
        return False


def start():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    config_file = CONFIG_DIR / "configuration.yaml"
    if not config_file.exists():
        print(f"[WARNING] {config_file} not found. Creating a default configuration...")
        write_default_config(config_file)

    if not is_docker_running():
        print("[ERROR] Docker is required but is not running or not installed.")
        print("[INFO] Install Docker Desktop/Engine and start the daemon before running this demo.")
        return False

    print("[INFO] Docker detected and running. Using Docker to spin up Home Assistant.")
    return start_docker()

def start_docker():
    # Check if container already exists
    res = subprocess.run(["docker", "ps", "-a", "--filter", f"name={CONTAINER_NAME}", "--format", "{{.Names}}"], stdout=subprocess.PIPE, text=True)
    
    if CONTAINER_NAME in res.stdout.split():
        print(f"[INFO] Container '{CONTAINER_NAME}' already exists. Starting it...")
        subprocess.run(["docker", "start", CONTAINER_NAME])
    else:
        # Convert config dir to absolute posix path for mounting compatibility in Docker
        config_posix = CONFIG_DIR.resolve().as_posix()
        print(f"[INFO] Creating and starting new container: {CONTAINER_NAME}")
        cmd = [
            "docker", "run", "-d",
            "--name", CONTAINER_NAME,
            "-p", "8123:8123",
            "-v", f"{config_posix}:/config",
            IMAGE_NAME
        ]
        print(f"[INFO] Executing command: {' '.join(cmd)}")
        run_res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if run_res.returncode != 0:
            print(f"[ERROR] Failed to run Docker container: {run_res.stderr}")
            return False
            
    print("\n[SUCCESS] Home Assistant is running in Docker!")
    print("[INFO] Access it at: http://localhost:8123")
    print("[INFO] (Note: When you first connect, you will be prompted to create an owner account.)")
    return True

def stop():
    if is_docker_running():
        stop_docker()
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            if is_pid_running(pid):
                print(f"[INFO] Removing stale PID file for process {pid}.")
        except Exception:
            pass
        PID_FILE.unlink(missing_ok=True)

def stop_docker():
    res = subprocess.run(["docker", "ps", "-a", "--filter", f"name={CONTAINER_NAME}", "--format", "{{.Names}}"], stdout=subprocess.PIPE, text=True)
    if CONTAINER_NAME in res.stdout.split():
        print(f"[INFO] Stopping Docker container '{CONTAINER_NAME}'...")
        subprocess.run(["docker", "stop", CONTAINER_NAME], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"[INFO] Removing Docker container '{CONTAINER_NAME}'...")
        subprocess.run(["docker", "rm", CONTAINER_NAME], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("[SUCCESS] Docker container stopped and removed.")

def is_pid_running(pid):
    if os.name == "nt":
        task_check = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], stdout=subprocess.PIPE, text=True)
        return str(pid) in task_check.stdout
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

def status():
    docker_up = False
    if is_docker_running():
        res = subprocess.run(["docker", "ps", "--filter", f"name={CONTAINER_NAME}", "--format", "{{.Status}}"], stdout=subprocess.PIPE, text=True)
        status_str = res.stdout.strip()
        if CONTAINER_NAME in status_str or status_str:
            print(f"[STATUS] Home Assistant (Docker) -> RUNNING ({status_str})")
            docker_up = True

    if not docker_up:
        print("[STATUS] Home Assistant -> STOPPED")

def logs():
    if is_docker_running():
        res = subprocess.run(["docker", "ps", "-a", "--filter", f"name={CONTAINER_NAME}", "--format", "{{.Names}}"], stdout=subprocess.PIPE, text=True)
        if CONTAINER_NAME in res.stdout.split():
            print("[INFO] Streaming logs from Docker container. Press Ctrl+C to stop.")
            try:
                subprocess.run(["docker", "logs", "-f", CONTAINER_NAME])
            except KeyboardInterrupt:
                print("\n[INFO] Stopped log streaming.")
            return

    print("[ERROR] No running Home Assistant instance found in Docker.")

def clean():
    print("[INFO] Initiating cleanup...")
    stop()

    for item in CONFIG_DIR.iterdir():
        if item.is_file() and item.name != "configuration.yaml":
            try:
                item.unlink()
            except Exception:
                pass
        elif item.is_dir():
            try:
                shutil.rmtree(item)
            except Exception:
                pass
                
    print("[SUCCESS] Cleanup finished.")

def write_default_config(filepath):
    # Default configuration with predefined API password and automatic auth setup
    config_content = """# Home Assistant Configuration
default_config:

api:

http:
  use_x_forwarded_for: true
  trusted_proxies:
    - 127.0.0.1
    - ::1
  api_password: "testing-key"

homeassistant:
  auth_providers:
    - type: trusted_networks
      trusted_networks:
        - 127.0.0.1
        - ::1
      allow_bypass_login: true
    - type: homeassistant

demo:
"""
    filepath.write_text(config_content, encoding="utf-8")

def main():
    print_banner()
    if len(sys.argv) < 2:
        print("Usage: python run_demo.py [start|stop|status|logs|clean]")
        sys.exit(1)
        
    cmd = sys.argv[1].lower()
    
    if cmd == "start":
        start()
    elif cmd == "stop":
        stop()
    elif cmd == "status":
        status()
    elif cmd == "logs":
        logs()
    elif cmd == "clean":
        clean()
    else:
        print(f"[ERROR] Unknown command: {cmd}")
        print("Usage: python run_demo.py [start|stop|status|logs|clean]")
        sys.exit(1)

if __name__ == "__main__":
    main()
