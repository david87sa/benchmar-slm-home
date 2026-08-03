import os
import socket
import subprocess
import sys


def get_device_name():
    env_value = os.getenv("DEVICE_NAME")
    if env_value and env_value.strip():
        return env_value.strip()

    if sys.platform.startswith("win"):
        commands = [
            ["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_ComputerSystem).Model"],
            ["wmic", "csproduct", "get", "name"],
        ]
        for command in commands:
            try:
                result = subprocess.run(command, capture_output=True, text=True, timeout=10)
                if result.returncode == 0:
                    for line in result.stdout.splitlines():
                        line = line.strip()
                        if line and not line.lower().startswith("name") and line.lower() != "model":
                            return line
            except Exception:
                continue

    if sys.platform == "darwin":
        try:
            result = subprocess.run(["sysctl", "-n", "hw.model"], capture_output=True, text=True, timeout=10)
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except Exception:
            pass

    try:
        with open("/sys/class/dmi/id/product_name", "r", encoding="utf-8") as fh:
            value = fh.read().strip()
            if value:
                return value
    except FileNotFoundError:
        pass

    return socket.gethostname()
