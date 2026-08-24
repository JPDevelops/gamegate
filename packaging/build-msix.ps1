<#
.SYNOPSIS
  Package the existing GameGate.exe into a signed GameGate.msix installer.

.DESCRIPTION
  Gives GameGate a real Windows app identity so it installs like a normal app
  and can be granted the notification-listener permission (a bare .exe can't).
  Creates a self-signed dev certificate the first time, signs the package with
  it, and exports the public cert so you can trust it before installing.

  Run from the repo root in PowerShell:  .\packaging\build-msix.ps1
  Then follow the printed steps to trust the cert and install the .msix.

.NOTES
  Requires the Windows 10/11 SDK (for makeappx.exe + signtool.exe) — install
  "Windows SDK" via Visual Studio Installer or standalone. Self-signed cert is
  fine for your own machine + the demo; a real code-signing cert is only needed
  to distribute to others without warnings. The cert Subject MUST match the
  Publisher in AppxManifest.xml (default: CN=JPDevelops).
#>
[CmdletBinding()]
param(
    [string]$ExePath    = "agent\dist\GameGate.exe",
    [string]$Manifest   = "packaging\AppxManifest.xml",
    [string]$AssetsDir  = "packaging\Assets",
    [string]$OutDir     = "packaging\out",
    [string]$CertSubject = "CN=JPDevelops"
)

$ErrorActionPreference = "Stop"
function Info($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Die($m)  { Write-Host "ERROR: $m" -ForegroundColor Red; exit 1 }

# --- locate SDK tools ---------------------------------------------------------
function Find-SdkTool($name) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $roots = @("${env:ProgramFiles(x86)}\Windows Kits\10\bin", "${env:ProgramFiles}\Windows Kits\10\bin")
    foreach ($r in $roots) {
        if (Test-Path $r) {
            $hit = Get-ChildItem $r -Recurse -Filter $name -ErrorAction SilentlyContinue |
                   Where-Object { $_.FullName -match "\\x64\\" } |
                   Sort-Object FullName -Descending | Select-Object -First 1
            if ($hit) { return $hit.FullName }
        }
    }
    return $null
}

$makeappx = Find-SdkTool "makeappx.exe"
$signtool = Find-SdkTool "signtool.exe"
if (-not $makeappx) { Die "makeappx.exe not found — install the Windows 10/11 SDK." }
if (-not $signtool) { Die "signtool.exe not found — install the Windows 10/11 SDK." }
Info "makeappx: $makeappx"
Info "signtool: $signtool"

if (-not (Test-Path $ExePath)) {
    Die "GameGate.exe not found at '$ExePath'. Build it first (see docs/DESKTOP_APP.md) or pass -ExePath."
}

# --- stage the package layout -------------------------------------------------
$stage = Join-Path $OutDir "stage"
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Path $stage | Out-Null
New-Item -ItemType Directory -Path (Join-Path $stage "Assets") | Out-Null

Copy-Item $ExePath (Join-Path $stage "GameGate.exe")
Copy-Item $Manifest (Join-Path $stage "AppxManifest.xml")
Copy-Item (Join-Path $AssetsDir "*") (Join-Path $stage "Assets")
# Ship an example config next to the exe so first run has something to copy.
if (Test-Path "agent\config.example.json") {
    Copy-Item "agent\config.example.json" (Join-Path $stage "config.example.json")
}
Info "Staged package at $stage"

# --- pack ---------------------------------------------------------------------
$msix = Join-Path $OutDir "GameGate.msix"
if (Test-Path $msix) { Remove-Item $msix -Force }
& $makeappx pack /d $stage /p $msix /overwrite
if ($LASTEXITCODE -ne 0) { Die "makeappx pack failed." }
Info "Built $msix"

# --- ensure a signing cert ----------------------------------------------------
$cert = Get-ChildItem Cert:\CurrentUser\My | Where-Object { $_.Subject -eq $CertSubject } | Select-Object -First 1
if (-not $cert) {
    Info "Creating self-signed dev cert $CertSubject"
    $cert = New-SelfSignedCertificate -Type Custom -Subject $CertSubject `
        -KeyUsage DigitalSignature -FriendlyName "GameGate Dev Cert" `
        -CertStoreLocation "Cert:\CurrentUser\My" `
        -TextExtension @("2.5.29.37={text}1.3.6.1.5.5.7.3.3", "2.5.29.19={text}")
}
$thumb = $cert.Thumbprint
Info "Signing with cert $thumb"

# Export the PUBLIC cert so the user can trust it before installing.
$cerPath = Join-Path $OutDir "GameGate-DevCert.cer"
Export-Certificate -Cert $cert -FilePath $cerPath | Out-Null

# --- sign ---------------------------------------------------------------------
& $signtool sign /fd SHA256 /sha1 $thumb /tr http://timestamp.digicert.com /td SHA256 $msix
if ($LASTEXITCODE -ne 0) { Die "signtool sign failed (is the cert usable for code signing?)." }
Info "Signed $msix"

Write-Host ""
Write-Host "DONE. Next steps to install on THIS machine:" -ForegroundColor Green
Write-Host "  1. Trust the dev cert (one time). In an ADMIN PowerShell:" -ForegroundColor Green
Write-Host "       Import-Certificate -FilePath `"$cerPath`" -CertStoreLocation Cert:\LocalMachine\TrustedPeople"
Write-Host "  2. Install the app:" -ForegroundColor Green
Write-Host "       Add-AppxPackage -Path `"$msix`""
Write-Host "     (or just double-click GameGate.msix once the cert is trusted)."
Write-Host "  3. Launch GameGate from the Start menu. On first run, say Yes to the"
Write-Host "     notification consent, then ALLOW the Windows 'access notifications' prompt."
Write-Host "  4. It will now appear under Settings > Privacy & security > Notifications."
