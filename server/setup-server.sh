#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

SKIP_INSTALL=false
SKIP_START=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-install)
      SKIP_INSTALL=true
      ;;
    --skip-start)
      SKIP_START=true
      ;;
    *)
      echo "Uso: $0 [--skip-install] [--skip-start]"
      exit 1
      ;;
  esac
  shift
done

echo "=================================================="
echo "  PREPARANDO HOME ASSISTANT DEMO (LINUX)          "
echo "=================================================="

if ! command -v python3 &>/dev/null; then
  echo "Python 3 no está instalado. Instalándolo con apt..."
  sudo apt update
  sudo apt install -y python3 python3-venv python3-pip
fi

if [ "$SKIP_INSTALL" = false ]; then
  echo "[1/2] Verificando Python 3..."
  if ! command -v python3 &>/dev/null; then
    echo "Python 3 no está instalado. Instalándolo con apt..."
    sudo apt update
    sudo apt install -y python3 python3-pip
  fi
fi

if command -v docker &>/dev/null; then
  echo "Docker detectado. Se comprobará que el daemon esté listo antes de arrancar el demo."
else
  echo "Docker no está instalado. Instálalo para usar este flujo." >&2
  exit 1
fi

for attempt in $(seq 1 12); do
  if docker info >/dev/null 2>&1; then
    DOCKER_READY=true
    break
  fi
  echo "Esperando a que Docker esté listo... ($attempt/12)"
  sleep 5
 done

if [ "${DOCKER_READY:-false}" != true ]; then
  echo "Docker no quedó disponible o no pudo arrancar. Inicia Docker Desktop/Engine y vuelve a ejecutar este script." >&2
  exit 1
fi

if ! command -v mosquitto &>/dev/null; then
  echo "Mosquitto no está instalado. Intentando instalarlo con apt..."
  sudo apt update
  sudo apt install -y mosquitto mosquitto-clients
fi

if [ "$SKIP_START" = false ]; then
  echo "Arrancando Mosquitto con configuración por defecto..."
  if pgrep -x mosquitto >/dev/null 2>&1; then
    echo "Mosquitto ya está en ejecución."
  else
    mosquitto -c "$SCRIPT_DIR/mosquitto.conf" >/tmp/mosquitto-demo.log 2>&1 &
  fi

  echo "Arrancando Home Assistant Demo..."
  python3 "$SCRIPT_DIR/run_demo.py" start
else
  echo "La preparación quedó lista. Para arrancarlo, ejecuta:"
  echo "  ./setup-server.sh"
fi

echo "=================================================="
echo "   ¡ENTORNO LISTO PARA HOME ASSISTANT DEMO!       "
echo "   Abre http://localhost:8123 cuando esté arriba.  "
echo "=================================================="
