; GameGate installer wizard (Inno Setup) — PER-USER, no admin, no MSIX.
;
; Wraps the plain GameGate.exe in a friendly wizard: Welcome -> What-it-does /
; consent -> Ready -> progress -> Finished (with "Launch now"). Installs into
; %LOCALAPPDATA%\Programs\GameGate (no UAC), adds a Start-menu shortcut, starts
; with Windows, and seeds a config so capture is on and the app doesn't re-ask
; (the consent page already covered it).
;
; Built in CI (see .github/workflows/release.yml) which drops GameGate.exe and
; installer-config.json next to this file, installs Inno Setup, and compiles.
;
; Inno entries are ONE LINE each. Wizard copy lives in [Messages] + consent.txt.

#define AppName "GameGate"
#define AppVersion "0.3.0"
#define AppExe "GameGate.exe"
#define Publisher "JPDevelops"

[Setup]
AppId={{8F2A7C10-5B3E-4D9A-9C21-7E4B0A2C9E7F}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#Publisher}
DefaultDirName={localappdata}\Programs\{#AppName}
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir=out
OutputBaseFilename=GameGateSetup
SetupIconFile=..\..\agent\gamegate.ico
UninstallDisplayIcon={app}\{#AppExe}
WizardStyle=modern
DisableWelcomePage=no
; The "what it does / agree" page shown before install.
InfoBeforeFile=consent.txt

[Files]
Source: "GameGate.exe"; DestDir: "{app}"; Flags: ignoreversion
; Seed config so capture is ON and the app skips its own consent (the wizard's
; consent page is the consent). Only if the user has no config yet.
Source: "installer-config.json"; DestDir: "{localappdata}\{#AppName}"; DestName: "config.json"; Flags: onlyifdoesntexist

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExe}"

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch GameGate now"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{localappdata}\{#AppName}"

[Messages]
WelcomeLabel1=Welcome to GameGate
WelcomeLabel2=Game now, catch up after.%n%nGameGate holds your notifications while you play and hands you one clean recap when you're done. It runs entirely on this PC.%n%nClick Next to set it up.
