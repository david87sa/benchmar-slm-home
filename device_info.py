import os
import socket
import subprocess
import sys

from logger import get_logger

logger = get_logger(__name__)


def get_device_name():
    env_value = os.getenv("DEVICE_NAME")
    if env_value and env_value.strip():
        logger.debug("Device name from DEVICE_NAME env: %s", env_value.strip())
        return env_value.strip()

    if sys.platform.startswith("win"):
        commands = [
            ["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_ComputerSystem).Model"],
            ["wmic", "csproduct", "get", "name"],
        ]
        for command in commands:
            try:
                logger.debug("Trying Windows command: %s", " ".join(command))
                result = subprocess.run(command, capture_output=True, text=True, timeout=10)
                if result.returncode == 0:
                    for line in result.stdout.splitlines():
                        line = line.strip()
                        if line and not line.lower().startswith("name") and line.lower() != "model":
                            logger.debug("Device name from Windows command: %s", line)
                            return line
            except Exception:
                continue

    if sys.platform == "darwin":
        try:
            logger.debug("Trying macOS sysctl hw.model")
            result = subprocess.run(["sysctl", "-n", "hw.model"], capture_output=True, text=True, timeout=10)
            if result.returncode == 0 and result.stdout.strip():
                logger.debug("Device name from macOS sysctl: %s", result.stdout.strip())
                return result.stdout.strip()
        except Exception:
            pass

    try:
        logger.debug("Trying /sys/class/dmi/id/product_name")
        with open("/sys/class/dmi/id/product_name", "r", encoding="utf-8") as fh:
            value = fh.read().strip()
            if value:
                logger.debug("Device name from DMI product_name: %s", value)
                return value
    except FileNotFoundError:
        pass

    hostname = socket.gethostname()
    logger.debug("Falling back to hostname: %s", hostname)
    return hostname