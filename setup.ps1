Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "  INSTALADOR DEL LABORATORIO EDGE AI (WINDOWS)   " -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan

# 1. Instalar Python 3.11/3.12 y Ollama usando Winget
Write-Host "[1/3] Instalando Python y Ollama vía Winget..." -ForegroundColor Yellow
winget install -e --id Python.Python.3.11 --accept-source-agreements --accept-package-agreements
winget install -e --id Ollama.Ollama --accept-source-agreements --accept-package-agreements

# Refrescar variables de entorno en la sesión actual
$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")

# 2. Instalar librerías de Python requeridas
Write-Host "[2/3] Instalando dependencias de Python..." -ForegroundColor Yellow
python -m pip install --upgrade pip
python -m pip install psutil requests paho-mqtt

# 3. Descargar los modelos en Ollama
Write-Host "[3/3] Descargando modelos SLM para el benchmark..." -ForegroundColor Yellow
Write-Host "Asegúrate de tener abierta la aplicación de Ollama en la barra de tareas si da error de conexión." -ForegroundColor DarkYellow

ollama pull qwen2.5:1.5b
ollama pull functiongemma
ollama pull phi4-mini:3.8b

# 4. Ejecutar el benchmark para cada modelo
$models = @("qwen2.5:1.5b", "functiongemma", "phi4-mini:3.8b")
foreach ($model in $models) {
    Write-Host "Ejecutando benchmark con el modelo: $model" -ForegroundColor Yellow
    python .\benchmark.py --model $model --log-level debug
}

Write-Host "==================================================" -ForegroundColor Green
Write-Host "   ¡INSTALACIÓN Y CONFIGURACIÓN COMPLETADA!       " -ForegroundColor Green
Write-Host "==================================================" -ForegroundColor Green