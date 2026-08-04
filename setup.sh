#!/bin/bash

echo "=================================================="
echo "   INSTALADOR DEL LABORATORIO EDGE AI (LINUX)    "
echo "=================================================="

# 1. Actualizar el sistema e instalar Python y Git
echo "[1/4] Actualizando sistema e instalando dependencias..."
sudo apt update && sudo apt install -y python3 python3-pip python3-venv git curl

# 2. Instalar Ollama de forma oficial (si no está ya instalado)
if command -v ollama &> /dev/null; then
  echo "[2/4] Ollama ya está instalado ($(ollama --version 2>/dev/null || echo 'versión desconocida')). Omitiendo instalación..."
else
  echo "[2/4] Instalando Ollama..."
  curl -fsSL https://ollama.com/install.sh | sh
fi

# Asegurar que el servicio de Ollama esté corriendo
sudo systemctl enable --now ollama

# Esperar unos segundos a que el daemon levante
sleep 5

# 3. Descargar la Matriz de Modelos Candidatos (SLMs)
echo "[3/4] Descargando modelos SLM objetivos..."
ollama pull qwen2.5:1.5b
ollama pull functiongemma
ollama pull phi4-mini

# 4. Configurar Entorno Virtual de Python e Instalar Librerías
echo "[4/4] Creando entorno virtual de Python e instalando paquetes..."
python3 -m venv venv_lab
source venv_lab/bin/activate
pip install --upgrade pip
pip install psutil requests paho-mqtt

# 5. Ejecutar el benchmark para cada modelo
for model in qwen2.5:1.5b functiongemma phi4-mini; do
  echo "Ejecutando benchmark con el modelo: $model"
  python benchmark.py --model "$model"
done

echo "=================================================="
echo "   ¡INSTALACIÓN COMPLETADA CON ÉXITO!            "
echo "   Para activar el entorno de Python ejecuta:   "
echo "   source venv_lab/bin/activate                   "
echo "=================================================="