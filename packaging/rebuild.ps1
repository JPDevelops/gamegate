<#
.SYNOPSIS
  One command to rebuild + reinstall GameGate from the latest source.

.DESCRIPTION
  Does the whole loop so you don't paste a wall of commands:
    1. close a running GameGate
    2. git pull
    3. rebuild GameGate.exe from GameGate.spec (bundles the embedded server)
    4. package + sign the MSIX (build-msix.ps1)
    5. remove the old package + install the new one (reinstall.ps1)

  Run from the repo root:  .\packaging\rebuild.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
function Info($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Die($m)  { Write-Host "ERROR: $m" -ForegroundColor Red; exit 1 }

# Always run relative to the repo root (this script lives in packaging\).
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

Info "Closing any running GameGate..."
taskkill /F /IM GameGate.exe 2>$null | Out-Null

Info "Pulling latest source..."
git pull

Info "Rebuilding GameGate.exe (bundles the server; this takes a minute)..."
Push-Location agent
try {
    Remove-Item "dist\GameGate.exe" -ErrorAction SilentlyContinue
    python -m PyInstaller GameGate.spec
} finally {
    Pop-Location
}
if (-not (Test-Path "agent\dist\GameGate.exe")) { Die "exe build failed - scroll up for the PyInstaller error." }

Info "Packaging + signing the MSIX..."
& "$PSScriptRoot\build-msix.ps1"

Info "Installing..."
& "$PSScriptRoot\reinstall.ps1"

Write-Host ""
Write-Host "All done. Launch GameGate from the Start menu." -ForegroundColor Green
