#!/usr/bin/env python3
import os
import sys
import subprocess
import time
import shutil
from pathlib import Path

# Add parent directory to sys.path so we can import the logger module
sys.path.insert(0, str(Path(__file__).parent.parent.resolve()))

from logger import configure_logging, get_logger

logger = get_logger(__name__)

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
        logger.warning("%s not found. Creating a default configuration...", config_file)
        write_default_config(config_file)

    if not is_docker_running():
        logger.error("Docker is required but is not running or not installed.")
        logger.info("Install Docker Desktop/Engine and start the daemon before running this demo.")
        return False

    logger.info("Docker detected and running. Using Docker to spin up Home Assistant.")
    return start_docker()

def start_docker():
    # Check if container already exists
    res = subprocess.run(["docker", "ps", "-a", "--filter", f"name={CONTAINER_NAME}", "--format", "{{.Names}}"], stdout=subprocess.PIPE, text=True)
    
    if CONTAINER_NAME in res.stdout.split():
        logger.info("Container '%s' already exists. Starting it...", CONTAINER_NAME)
        subprocess.run(["docker", "start", CONTAINER_NAME])
    else:
        # Convert config dir to absolute posix path for mounting compatibility in Docker
        config_posix = CONFIG_DIR.resolve().as_posix()
        logger.info("Creating and starting new container: %s", CONTAINER_NAME)
        cmd = [
            "docker", "run", "-d",
            "--name", CONTAINER_NAME,
            "-p", "8123:8123",
            "-v", f"{config_posix}:/config",
            IMAGE_NAME
        ]
        logger.debug("Executing command: %s", " ".join(cmd))
        run_res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if run_res.returncode != 0:
            logger.error("Failed to run Docker container: %s", run_res.stderr)
            return False

    logger.info("Home Assistant is running in Docker!")
    logger.info("Access it at: http://localhost:8123")
    logger.info("(Note: When you first connect, you will be prompted to create an owner account.)")
    return True

def stop():
    if is_docker_running():
        stop_docker()
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            if is_pid_running(pid):
                logger.info("Removing stale PID file for process %s.", pid)
        except Exception:
            pass
        PID_FILE.unlink(missing_ok=True)

def stop_docker():
    res = subprocess.run(["docker", "ps", "-a", "--filter", f"name={CONTAINER_NAME}", "--format", "{{.Names}}"], stdout=subprocess.PIPE, text=True)
    if CONTAINER_NAME in res.stdout.split():
        logger.info("Stopping Docker container '%s'...", CONTAINER_NAME)
        subprocess.run(["docker", "stop", CONTAINER_NAME], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        logger.info("Removing Docker container '%s'...", CONTAINER_NAME)
        subprocess.run(["docker", "rm", CONTAINER_NAME], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        logger.info("Docker container stopped and removed.")

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
            logger.info("Home Assistant (Docker) -> RUNNING (%s)", status_str)
            docker_up = True

    if not docker_up:
        logger.info("Home Assistant -> STOPPED")

def logs():
    if is_docker_running():
        res = subprocess.run(["docker", "ps", "-a", "--filter", f"name={CONTAINER_NAME}", "--format", "{{.Names}}"], stdout=subprocess.PIPE, text=True)
        if CONTAINER_NAME in res.stdout.split():
            logger.info("Streaming logs from Docker container. Press Ctrl+C to stop.")
            try:
                subprocess.run(["docker", "logs", "-f", CONTAINER_NAME])
            except KeyboardInterrupt:
                logger.info("Stopped log streaming.")
            return

    logger.error("No running Home Assistant instance found in Docker.")

def clean():
    logger.info("Initiating cleanup...")
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
                
    logger.info("Cleanup finished.")

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
        print("Usage: python run_demo.py [start|stop|status|logs|clean] [--log-level LEVEL]")
        sys.exit(1)

    # Parse --log-level if present
    log_level = None
    cmd_args = []
    for arg in sys.argv[1:]:
        if arg == "--log-level":
            continue
        elif cmd_args and cmd_args[-1] == "--log-level":
            log_level = arg
        elif arg.startswith("--log-level="):
            log_level = arg.split("=", 1)[1]
        else:
            cmd_args.append(arg)

    configure_logging(cli_level=log_level)

    if not cmd_args:
        print("Usage: python run_demo.py [start|stop|status|logs|clean] [--log-level LEVEL]")
        sys.exit(1)

    cmd = cmd_args[0].lower()

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
        logger.error("Unknown command: %s", cmd)
        print("Usage: python run_demo.py [start|stop|status|logs|clean] [--log-level LEVEL]")
        sys.exit(1)

if __name__ == "__main__":
    main()
