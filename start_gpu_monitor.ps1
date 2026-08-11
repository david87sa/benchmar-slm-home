# start_gpu_monitor.ps1
# Opens the live NVIDIA GPU monitor (monitor_gpu.py) in a NEW, separate console window.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\start_gpu_monitor.ps1
#   .\start_gpu_monitor.ps1 --refresh 2
#   .\start_gpu_monitor.ps1 --log-level debug
#
# Extra arguments are forwarded to monitor_gpu.py.

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path

# Locate the Python interpreter
$pythonExe = $null
foreach ($candidate in @("python", "py")) {
    $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($cmd) {
        $pythonExe = $cmd.Source
        break
    }
}

if (-not $pythonExe) {
    Write-Error "Python 3 was not found. Install Python 3 and add it to PATH."
    exit 1
}

# Build the argument list for monitor_gpu.py (-u = unbuffered output)
$baseArgs = @("-u", "monitor_gpu.py")
$extraArgs = @($args | ForEach-Object { [string]$_ })

Write-Host "Opening GPU monitor in a new console window..." -ForegroundColor Cyan

# Start-Process launches console applications in a brand-new console window.
Start-Process -FilePath $pythonExe `
    -ArgumentList ($baseArgs + $extraArgs) `
    -WorkingDirectory $root `
    -WindowStyle Normal

Write-Host ""
Write-Host "The 'GPU Monitor' window is now running in the background." -ForegroundColor DarkGray
Write-Host "To stop it: press Ctrl+C inside that window, or close it." -ForegroundColor DarkGray