# GameGate self-updater: silent console, styled progress window, auto-restart.
# Spawned hidden by the tray's "Update GameGate" item. ASCII ONLY in this file:
# PowerShell 5.1 reads BOM-less scripts as ANSI and non-ASCII becomes syntax.
$ErrorActionPreference = "Continue"
$agent = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo  = Split-Path -Parent $agent
$log   = Join-Path $agent "update.log"

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$form = New-Object System.Windows.Forms.Form
$form.FormBorderStyle = "None"
$form.Size = New-Object System.Drawing.Size(380, 96)
$form.BackColor = [System.Drawing.ColorTranslator]::FromHtml("#0e1011")
$form.TopMost = $true
$form.ShowInTaskbar = $false
$screen = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
$form.StartPosition = "Manual"
$form.Location = New-Object System.Drawing.Point(($screen.Width - 396), 16)

$title = New-Object System.Windows.Forms.Label
$title.Text = "Updating GameGate"
$title.ForeColor = [System.Drawing.ColorTranslator]::FromHtml("#e7eae8")
$title.Font = New-Object System.Drawing.Font("Segoe UI", 10, [System.Drawing.FontStyle]::Bold)
$title.Location = New-Object System.Drawing.Point(16, 12)
$title.AutoSize = $true
$form.Controls.Add($title)

$stage = New-Object System.Windows.Forms.Label
$stage.Text = "Starting..."
$stage.ForeColor = [System.Drawing.ColorTranslator]::FromHtml("#7e8681")
$stage.Font = New-Object System.Drawing.Font("Segoe UI", 8.5)
$stage.Location = New-Object System.Drawing.Point(16, 34)
$stage.AutoSize = $true
$form.Controls.Add($stage)

$bar = New-Object System.Windows.Forms.ProgressBar
$bar.Minimum = 0; $bar.Maximum = 100; $bar.Value = 5
$bar.Style = "Continuous"
$bar.Location = New-Object System.Drawing.Point(16, 58)
$bar.Size = New-Object System.Drawing.Size(348, 14)
$form.Controls.Add($bar)

$form.Show()
[System.Windows.Forms.Application]::DoEvents()

function Step($pct, $txt) {
  $bar.Value = $pct
  $stage.Text = $txt
  [System.Windows.Forms.Application]::DoEvents()
}

function Fail($why) {
  $form.Close()
  [System.Windows.Forms.MessageBox]::Show(
    "GameGate update failed: $why`nDetails in agent\update.log",
    "GameGate Update", "OK", "Error") | Out-Null
  exit 1
}

Step 10 "Closing GameGate..."
Start-Sleep -Seconds 2
taskkill /F /IM GameGate.exe 2>$null | Out-Null

Step 25 "Downloading the latest version..."
Set-Location $repo
git pull *> $log
if ($LASTEXITCODE -ne 0) { Fail "download (git pull) failed" }

Set-Location $agent
$hash = git rev-parse --short HEAD
$stamp = Get-Date -Format "MMM d HH:mm"
"{`"build`": `"$hash`", `"built`": `"$stamp`"}" | Out-File -Encoding utf8 build_info.json

Step 45 "Building (about 30 seconds)..."
# Keep the working exe as a backup instead of deleting it up front, so a failed
# build rolls back to the previous version instead of leaving no app at all.
$backup = "dist\GameGate.exe.bak"
if (Test-Path "dist\GameGate.exe") {
  if (Test-Path $backup) { Remove-Item $backup -Force }
  Rename-Item "dist\GameGate.exe" $backup
}
python -m PyInstaller --onefile --noconsole --name GameGate --icon gamegate.ico tray_app.py *>> $log
if (-not (Test-Path "dist\GameGate.exe")) {
  if (Test-Path $backup) { Rename-Item $backup "dist\GameGate.exe" }
  Fail "build failed (previous version restored)"
}
if (Test-Path $backup) { Remove-Item $backup -Force }

Step 90 "Finishing up..."
if (Test-Path "config.json") { Copy-Item "config.json" "dist\" -Force }
if (Test-Path "build_info.json") { Copy-Item "build_info.json" "dist\" -Force }

Step 100 "Restarting GameGate..."
Start-Process "$agent\dist\GameGate.exe"
Start-Sleep -Seconds 1
$form.Close()
