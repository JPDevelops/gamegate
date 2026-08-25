; GameGate end-user installer.
;
; Bundles the signed MSIX + its dev certificate into ONE GameGateSetup.exe so a
; normal person just double-clicks and clicks through a wizard. On install it
; trusts the certificate (LocalMachine\TrustedPeople) and installs the MSIX.
;
; Build:   .\build-installer.ps1        (from this folder; needs Inno Setup 6)
; Needs:   ..\out\GameGate.msix and ..\out\GameGate-DevCert.cer to exist first
;          (produce them with ..\build-msix.ps1).
;
; NOTE: with the free self-signed cert the user still sees ONE Windows elevation
; prompt (UAC) and possibly a SmartScreen "More info -> Run anyway". A paid
; code-signing certificate is the only way to remove those entirely.

#define AppName "GameGate"
#define AppVersion "0.2.4.0"
#define Publisher "JPDevelops"
; PackageFamilyName!AppId — used to launch the installed app at the end.
#define Aumid "JPDevelops.GameGate_dv2k48eds5q3y!GameGate"

[Setup]
AppId={{8F2A7C10-5B3E-4D9A-9C21-7E4B0A2C9E7F}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#Publisher}
DefaultDirName={autopf}\{#AppName}
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=admin
OutputDir=..\out
OutputBaseFilename=GameGateSetup
SetupIconFile=..\..\agent\gamegate.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\gamegate.ico

[Messages]
WelcomeLabel2=This installs GameGate on your computer.%n%nGameGate holds your notifications while you game and hands you one clean recap after. It runs entirely on this PC — no account, no server to set up.

[Files]
Source: "..\out\GameGate.msix";         DestDir: "{tmp}"; Flags: deleteafterinstall
Source: "..\out\GameGate-DevCert.cer";  DestDir: "{tmp}"; Flags: deleteafterinstall
Source: "..\..\agent\gamegate.ico";     DestDir: "{app}"; Flags: ignoreversion

[Run]
; 1) Trust the publisher certificate so Windows will accept the package.
Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""Import-Certificate -FilePath '{tmp}\GameGate-DevCert.cer' -CertStoreLocation Cert:\LocalMachine\TrustedPeople"""; \
  StatusMsg: "Trusting the GameGate certificate..."; Flags: runhidden waituntilterminated

; 2) Install the app package.
Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""Add-AppxPackage -Path '{tmp}\GameGate.msix' -ForceUpdateFromAnyVersion"""; \
  StatusMsg: "Installing GameGate..."; Flags: runhidden waituntilterminated

; 3) Offer to launch it (checkbox on the finish page).
Filename: "explorer.exe"; Parameters: "shell:AppsFolder\{#Aumid}"; \
  Description: "Launch GameGate now"; Flags: postinstall nowait skipifsilent

[UninstallRun]
Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""Get-AppxPackage -Name JPDevelops.GameGate | Remove-AppxPackage"""; \
  Flags: runhidden; RunOnceId: "RemoveGameGateAppx"
