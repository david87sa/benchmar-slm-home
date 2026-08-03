[CmdletBinding()]
param(
    [switch]$SkipInstall,
    [switch]$SkipDockerInstall,
    [switch]$SkipStart
)

$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

function Invoke-Python {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    if (Get-Command python -ErrorAction SilentlyContinue) {
        & python @Arguments
    }
    elseif (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3 @Arguments
    }
    else {
        throw "No se encontró Python. Instálalo o reinicia PowerShell después de la instalación."
    }

    if ($LASTEXITCODE -ne 0) {
        throw "El comando Python falló con código $LASTEXITCODE"
    }
}

function Test-DockerReady {
    $dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
    if (-not $dockerCommand) {
        return $false
    }

    try {
        & $dockerCommand.Source info 2>$null | Out-Null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
}

function Start-DockerDesktop {
    $dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
    if (-not $dockerCommand) {
        return $false
    }

    try {
        & $dockerCommand.Source desktop start 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) {
            return $true
        }
    }
    catch {
        # Ignore and fall back to launching the Desktop app directly.
    }

    $desktopCandidates = @(
        "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe",
        "$env:ProgramFiles(x86)\Docker\Docker\Docker Desktop.exe"
    )

    foreach ($candidate in $desktopCandidates) {
        if (Test-Path $candidate) {
            Start-Process -FilePath $candidate -WindowStyle Normal
            return $true
        }
    }

    return $false
}

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "  PREPARANDO HOME ASSISTANT DEMO (WINDOWS)        " -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan

if (-not $SkipInstall) {
    Write-Host "[1/3] Verificando Python..." -ForegroundColor Yellow
    if (-not (Get-Command python -ErrorAction SilentlyContinue) -and -not (Get-Command py -ErrorAction SilentlyContinue)) {
        Write-Host "Python no está disponible. Intentando instalarlo con Winget..." -ForegroundColor Yellow
        & winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
    }
}

if (-not $SkipDockerInstall) {
    Write-Host "[2/3] Verificando Docker..." -ForegroundColor Yellow
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Write-Host "Docker no está instalado. Intentando instalar Docker Desktop..." -ForegroundColor Yellow
        & winget install -e --id Docker.DockerDesktop --accept-source-agreements --accept-package-agreements
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
    }

    $dockerReady = $false
    for ($attempt = 1; $attempt -le 20; $attempt++) {
        if (Test-DockerReady) {
            $dockerReady = $true
            break
        }

        if ($attempt -eq 1) {
            Write-Host "Intentando arrancar Docker Desktop..." -ForegroundColor Yellow
            Start-DockerDesktop | Out-Null
        }

        Write-Host "Esperando a que Docker esté listo... ($attempt/20)" -ForegroundColor DarkYellow
        Start-Sleep -Seconds 5
    }

    if (-not $dockerReady) {
        throw "Docker no quedó disponible o no pudo arrancar. Abre Docker Desktop/Engine manualmente y vuelve a ejecutar este script."
    }

    Write-Host "[3/3] Verificando Mosquitto..." -ForegroundColor Yellow
    if (-not (Get-Command mosquitto -ErrorAction SilentlyContinue)) {
        Write-Host "Mosquitto no está instalado. Intentando instalarlo con Winget..." -ForegroundColor Yellow
        & winget install -e --id EclipseFoundation.Mosquitto --accept-source-agreements --accept-package-agreements
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
    }
}

if (-not $SkipStart) {
    Write-Host "Arrancando Mosquitto con configuración local..." -ForegroundColor Green
    $mosquittoConfig = Join-Path $scriptDir 'mosquitto.conf'
    
    Write-Host "Configuración local... $mosquittoConfig" -ForegroundColor Green
    $mosquittoProcess = Get-Process mosquitto -ErrorAction SilentlyContinue
    if ($mosquittoProcess) {
        Write-Host "Mosquitto ya estaba en ejecución. Cerrándolo para reiniciarlo con la configuración correcta..." -ForegroundColor Yellow
        try {
            Stop-Process -Id $mosquittoProcess.Id -Force
        }
        catch {
            Write-Host "No se pudo detener con permisos normales. Intentando con taskkill..." -ForegroundColor Yellow
            try {
                & taskkill /PID $mosquittoProcess.Id /F | Out-Null
            }
            catch {
                Write-Warning "No se pudo detener Mosquitto automáticamente. Cierra el proceso manualmente y vuelve a ejecutar el script."
            }
        }
        Start-Sleep -Seconds 2
    }

    $mosquittoCommand = Get-Command mosquitto -ErrorAction SilentlyContinue
    if (-not $mosquittoCommand) {
        $candidatePaths = @(
            "$env:ProgramFiles\\mosquitto\\mosquitto.exe",
            "$env:ProgramFiles(x86)\\mosquitto\\mosquitto.exe",
            "$env:LOCALAPPDATA\\Programs\\mosquitto\\mosquitto.exe"
        )
        foreach ($candidate in $candidatePaths) {
            if (Test-Path $candidate) {
                $mosquittoCommand = [pscustomobject]@{ Source = $candidate }
                break
            }
        }
    }

    if (-not $mosquittoCommand) {
        throw "No se encontró el ejecutable de Mosquitto en PATH ni en las rutas típicas de Windows. Instálalo o reinstala el paquete para que 'mosquitto' sea accesible."
    }

    if (Test-Path $mosquittoConfig) {
        Start-Process $mosquittoCommand.Source -ArgumentList "-c", $mosquittoConfig -WindowStyle Hidden
    }
    else {
        Start-Process $mosquittoCommand.Source -WindowStyle Hidden
    }

    Write-Host "Arrancando Home Assistant Demo..." -ForegroundColor Green
    Invoke-Python -Arguments @('.\run_demo.py', 'start')
}
else {
    Write-Host "La preparación quedó lista. Para arrancarlo, ejecuta:" -ForegroundColor Green
    Write-Host "  .\setup-server.ps1" -ForegroundColor Green
}

Write-Host "==================================================" -ForegroundColor Green
Write-Host "   ¡ENTORNO LISTO PARA HOME ASSISTANT DEMO!        " -ForegroundColor Green
Write-Host "   Abre http://localhost:8123 cuando esté arriba.  " -ForegroundColor Green
Write-Host "==================================================" -ForegroundColor Green
