<#
.SYNOPSIS
  Compile GameGate.iss into one GameGateSetup.exe using Inno Setup.

.DESCRIPTION
  Produces ..\out\GameGateSetup.exe — the single file you hand to an end user.
  They double-click it, click through the wizard, and GameGate installs (trusts
  the cert + installs the MSIX) with one UAC prompt.

  Prereqs:
    1. Inno Setup 6 (free): https://jrsoftware.org/isdl.php  (install once)
    2. ..\out\GameGate.msix + ..\out\GameGate-DevCert.cer — run ..\build-msix.ps1
       first (or the full .\packaging\rebuild.ps1).

  Run from this folder:  .\build-installer.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
function Die($m) { Write-Host "ERROR: $m" -ForegroundColor Red; exit 1 }

$here = $PSScriptRoot
$msix = Join-Path $here "..\out\GameGate.msix"
$cer  = Join-Path $here "..\out\GameGate-DevCert.cer"
if (-not (Test-Path $msix)) { Die "GameGate.msix not found. Run .\packaging\build-msix.ps1 (or rebuild.ps1) first." }
if (-not (Test-Path $cer))  { Die "GameGate-DevCert.cer not found. Run .\packaging\build-msix.ps1 first." }

# Locate the Inno Setup compiler (ISCC.exe).
$iscc = (Get-Command iscc.exe -ErrorAction SilentlyContinue).Source
if (-not $iscc) {
    foreach ($p in @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles}\Inno Setup 6\ISCC.exe"
    )) { if (Test-Path $p) { $iscc = $p; break } }
}
if (-not $iscc) {
    Die "Inno Setup not found. Install it (free, 2 min): https://jrsoftware.org/isdl.php  then re-run this."
}
Write-Host "==> Using $iscc" -ForegroundColor Cyan

& $iscc (Join-Path $here "GameGate.iss")
if ($LASTEXITCODE -ne 0) { Die "Inno Setup compile failed (scroll up)." }

Write-Host ""
Write-Host "DONE. Your installer is: packaging\out\GameGateSetup.exe" -ForegroundColor Green
Write-Host "That single file is what you give someone to install GameGate." -ForegroundColor Green
