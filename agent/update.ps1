# GameGate self-updater (dev machine): pull latest, rebuild, relaunch.
# Spawned by the tray's "Update GameGate" menu item; the app quits right
# after spawning so the exe file is unlocked for the rebuild.
$ErrorActionPreference = "Continue"
$agent = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo  = Split-Path -Parent $agent

Write-Host ""
Write-Host "=== GameGate updater ===" -ForegroundColor Magenta
Start-Sleep -Seconds 2
taskkill /F /IM GameGate.exe 2>$null | Out-Null

Set-Location $repo
Write-Host "[1/4] Pulling latest..." -ForegroundColor Cyan
git pull

Set-Location $agent
Write-Host "[2/4] Building GameGate.exe (takes ~30s)..." -ForegroundColor Cyan
if (Test-Path "dist\GameGate.exe") { Remove-Item "dist\GameGate.exe" -Force }
python -m PyInstaller --onefile --noconsole --name GameGate --icon gamegate.ico tray_app.py

if (-not (Test-Path "dist\GameGate.exe")) {
  Write-Host "BUILD FAILED — see output above. Press Enter to close." -ForegroundColor Red
  Read-Host
  exit 1
}

Write-Host "[3/4] Copying config..." -ForegroundColor Cyan
if (Test-Path "config.json") { Copy-Item "config.json" "dist\" -Force }

Write-Host "[4/4] Launching the new GameGate..." -ForegroundColor Cyan
Start-Process "$agent\dist\GameGate.exe"
Write-Host "Done! This window closes in 5 seconds." -ForegroundColor Green
Start-Sleep -Seconds 5
