# Packaging GameGate as a real installed Windows app (MSIX)

Turning the loose `GameGate.exe` into an **installed** app gives it a Windows
*app identity*. That does two things:

1. It installs/looks like a real product — Start-menu entry, in the Apps list,
   its own icon and identity (like downloading Discord).
2. It's the **only** way Windows will grant the notification-listener permission,
   so the "capture every Windows notification" feature can actually work. (A bare
   `.exe` has no identity, so Windows never lets it read notifications.)

This is a Windows-only build. It was authored on Linux and **needs testing on
your machine** — expect a couple of iterations.

## One-time prerequisites

- **Windows 10/11 SDK** (provides `makeappx.exe` + `signtool.exe`). Install via
  the Visual Studio Installer ("Windows 11 SDK") or the standalone SDK.
- Build `agent/dist/GameGate.exe` first (PyInstaller — see `docs/DESKTOP_APP.md`
  or the release workflow). The build script packages that exe.

## Build + sign + install

From the repo root, in **PowerShell**:

```powershell
.\packaging\build-msix.ps1
```

It will:
1. Stage the app + `packaging\AppxManifest.xml` + `packaging\Assets\` into a
   package layout.
2. `makeappx pack` → `packaging\out\GameGate.msix`.
3. Create a **self-signed dev certificate** (subject `CN=JPDevelops`) the first
   time, and export its public part to `packaging\out\GameGate-DevCert.cer`.
4. Sign the `.msix` with it.

Then, as the script prints, install it:

```powershell
# 1) Trust the dev cert ONCE (admin PowerShell):
Import-Certificate -FilePath ".\packaging\out\GameGate-DevCert.cer" -CertStoreLocation Cert:\LocalMachine\TrustedPeople
# 2) Install:
Add-AppxPackage -Path ".\packaging\out\GameGate.msix"
```

(A self-signed cert is fine for your own machine and the demo. A paid
code-signing cert is only needed to hand the installer to strangers without a
SmartScreen warning.)

## After it installs

- Launch **GameGate** from the Start menu.
- Because the install folder is read-only, the app now keeps its `config.json`
  in **`%LOCALAPPDATA%\GameGate\config.json`** (seeded from the bundled
  `config.example.json`). Put your real `api_url` + `api_token` there.
- On first run, say **Yes** to the notification consent, then **Allow** the
  Windows "let GameGate access your notifications?" prompt.
- GameGate will now appear under **Settings → Privacy & security →
  Notifications**, and captured notifications flow into GameGate.

## Notes / gotchas

- The **Publisher** in `AppxManifest.xml` must exactly match the signing cert's
  subject. The script keeps both at `CN=JPDevelops` by default — change both if
  you change one.
- Bump `Version` in the manifest for each new package you install over an old one.
- The app icons under `packaging\Assets\` are generated from `agent/branding.py`
  (the green ring). Regenerate them if the branding changes.
