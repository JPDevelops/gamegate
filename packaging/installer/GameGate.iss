; GameGate installer wizard (Inno Setup) — PER-USER, no admin, no MSIX.
;
; Flow: Welcome -> "How GameGate works" (consent, gated checkbox) -> Ready ->
; Installing -> Finished (Launch now). Installs into
; %LOCALAPPDATA%\Programs\GameGate (no UAC), adds a Start-menu shortcut, starts
; with Windows, and seeds a config so capture is on and the app doesn't re-ask
; (the consent page IS the consent — there is no OS-level notification prompt).
;
; Built in CI (.github/workflows/release.yml): it drops GameGate.exe next to this
; file, installs Inno Setup, and compiles -> out\GameGateSetup.exe.
;
; Inno directives/entries are ONE LINE each. Copy lives in [Messages] + [Code].

#define AppName "GameGate"
#define AppVersion "0.5.11"
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
WizardImageFile=wizard-large.bmp
WizardSmallImageFile=wizard-small.bmp
DisableWelcomePage=no

[Files]
Source: "GameGate.exe"; DestDir: "{app}"; Flags: ignoreversion
; Seed config so capture is ON and the app skips its own consent (the wizard's
; consent page is the consent). Only if the user has no config yet.
Source: "installer-config.json"; DestDir: "{localappdata}\{#AppName}"; DestName: "config.json"; Flags: onlyifdoesntexist

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExe}"

[Run]
Filename: "{app}\{#AppExe}"; Parameters: "--show"; Description: "Launch GameGate now"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{localappdata}\{#AppName}"

[Messages]
WelcomeLabel1=Welcome to GameGate
WelcomeLabel2=GameGate holds your notifications while you game, then hands you one clean recap when you're done.%n%nIt runs entirely on this PC. No account, nothing to set up.%n%nGame now, catch up after.
ReadyLabel1=GameGate will be installed just for you, in your own user folder - so Windows won't ask for an administrator password.%n%nThis takes about ten seconds.
WizardInstalling=Installing GameGate
InstallingLabel=Setting things up. This will only take a moment.
FinishedHeadingLabel=GameGate is ready
FinishedLabel=GameGate is installed, and it'll start automatically with Windows so it's always ready when you are.%n%nLook for the green ring in your system tray, next to the clock - that's where your recap shows up.%n%nGame now, catch up after.
FinishedLabelNoIcons=GameGate is installed, and it'll start automatically with Windows so it's always ready when you are.%n%nLook for the green ring in your system tray, next to the clock - that's where your recap shows up.%n%nGame now, catch up after.

[Code]
var
  ConsentPage: TInputOptionWizardPage;

procedure ConsentCheckClick(Sender: TObject);
begin
  { Enable Next only once the box is ticked — no dead button without a reason. }
  WizardForm.NextButton.Enabled := ConsentPage.Values[0];
end;

procedure InitializeWizard;
begin
  ConsentPage := CreateInputOptionPage(wpWelcome,
    'How GameGate works',
    'One thing to okay before we install.',
    'To hold your notifications, GameGate reads the notifications this PC receives - the app name, the title, and the message text.' + #13#10#13#10 +
    'All of that stays on this computer. Nothing is uploaded, nothing is shared, and no one else can see it.' + #13#10#13#10 +
    'You can pause GameGate any time from the tray icon, or uninstall it like any other app.',
    False, False);
  ConsentPage.Add('I understand - GameGate can read this PC''s notifications.');
  ConsentPage.CheckListBox.OnClickCheck := @ConsentCheckClick;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  { Reflect the checkbox state whenever we (re)enter the consent page. }
  if CurPageID = ConsentPage.ID then
    WizardForm.NextButton.Enabled := ConsentPage.Values[0];
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = ConsentPage.ID then
    Result := ConsentPage.Values[0];
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  { A per-user app copies almost instantly; a brief, honest pause keeps the
    progress from flashing by so it reads as "it did something". }
  if CurStep = ssInstall then
  begin
    WizardForm.StatusLabel.Caption := 'Setting up GameGate...';
    Sleep(1200);
  end;
end;
