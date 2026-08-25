<#
.SYNOPSIS
  Close, uninstall, and (re)install the GameGate MSIX in one step.

.DESCRIPTION
  The raw Add-AppxPackage dance is fiddly: you must close the running app, then
  remove the already-installed package by its EXACT identity (name + version +
  publisher hash) before a rebuilt package with the same version can install.
  Typing that identity by hand is error-prone (a dropped '*' or an eaten '__'
  and the command silently matches nothing). This script does the whole dance
  with NO arguments to mistype:

    1. taskkill any running GameGate.exe
    2. remove any installed JPDevelops.GameGate package (matched by Name, so no
       wildcards or version hashes to get wrong)
    3. install packaging\out\GameGate.msix
    4. print the installed version

  Run it AFTER build-msix.ps1 has produced packaging\out\GameGate.msix:
      .\packaging\reinstall.ps1
#>
[CmdletBinding()]
param([string]$Msix = "packaging\out\GameGate.msix")

function Info($m) { Write-Host "==> $m" -ForegroundColor Cyan }

Info "Closing any running GameGate..."
taskkill /F /IM GameGate.exe 2>$null | Out-Null
Start-Sleep -Seconds 1

Info "Removing any installed GameGate package..."
Get-AppxPackage | Where-Object { $_.Name -eq "JPDevelops.GameGate" } | ForEach-Object {
    Info "  removing $($_.PackageFullName)"
    Remove-AppxPackage $_.PackageFullName
}

if (-not (Test-Path $Msix)) {
    Write-Host "ERROR: $Msix not found - run .\packaging\build-msix.ps1 first." -ForegroundColor Red
    exit 1
}

Info "Installing $Msix..."
Add-AppxPackage -Path $Msix

$pkg = Get-AppxPackage | Where-Object { $_.Name -eq "JPDevelops.GameGate" }
Write-Host ""
if ($pkg) {
    Write-Host "INSTALLED: $($pkg.Name)  $($pkg.Version)" -ForegroundColor Green
    Write-Host "Now launch GameGate from the Start menu." -ForegroundColor Green
} else {
    Write-Host "Package NOT found after install - something went wrong; paste the output above." -ForegroundColor Red
}
