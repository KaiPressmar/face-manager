#define MyAppName "Face Manager"
#define MyAppPublisher "Face Manager"
#define MyAppExeName "FaceManager.exe"
#ifndef InstallerSuffix
  #define InstallerSuffix ""
#endif

[Setup]
AppId={{0E1A7413-4FD3-4A24-A59A-3A4B359EA5D0}
AppName={#MyAppName}
AppVersion={#AppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
OutputDir={#OutputDir}
OutputBaseFilename=FaceManager-Setup{#InstallerSuffix}-{#AppVersion}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
SetupIconFile={#SourceDir}\packaging\windows\assets\face-manager-icon.ico
CloseApplications=yes
CloseApplicationsFilter={#MyAppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\dist\FaceManager\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; IconFilename: "{app}\{#MyAppExeName}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
var
  ExistingInstallPage: TWizardPage;
  ExistingInstallInfoLabel: TNewStaticText;
  UpdateInstallRadio: TNewRadioButton;
  ReplaceInstallRadio: TNewRadioButton;
  ExistingInstallDetected: Boolean;
  ExistingInstallVersion: string;
  ExistingInstallDir: string;
  ExistingUninstallString: string;
  ExistingInstallHandled: Boolean;
  ExistingUninstallExe: string;
  ExistingUninstallParams: string;

function AppDataDir(): string;
begin
  Result := ExpandConstant('{localappdata}\FaceManager');
end;

function UninstallKeyName(): string;
begin
  Result := '{0E1A7413-4FD3-4A24-A59A-3A4B359EA5D0}_is1';
end;

function TryGetExistingInstallValue(ValueName: string; var Value: string): Boolean;
var
  KeyName: string;
begin
  KeyName := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\' + UninstallKeyName();
  Result :=
    RegQueryStringValue(HKCU, KeyName, ValueName, Value) or
    RegQueryStringValue(HKLM, KeyName, ValueName, Value);
end;

function RemoveQuotes(const Value: string): string;
begin
  Result := Value;
  if (Length(Result) >= 2) and (Result[1] = '"') and (Result[Length(Result)] = '"') then
    Result := Copy(Result, 2, Length(Result) - 2);
end;

function TrimLeftWhitespace(const Value: string): string;
var
  Index: Integer;
begin
  Index := 1;
  while (Index <= Length(Value)) and (Value[Index] <= ' ') do
    Index := Index + 1;
  Result := Copy(Value, Index, MaxInt);
end;

procedure SplitCommandLine(const CommandLine: string; var FileName: string; var Params: string);
var
  Text: string;
  Index: Integer;
begin
  Text := TrimLeftWhitespace(CommandLine);
  FileName := '';
  Params := '';

  if Text = '' then
    exit;

  if Text[1] = '"' then
  begin
    Index := 2;
    while (Index <= Length(Text)) and (Text[Index] <> '"') do
      Index := Index + 1;
    FileName := Copy(Text, 2, Index - 2);
    Params := TrimLeftWhitespace(Copy(Text, Index + 1, MaxInt));
  end
  else
  begin
    Index := 1;
    while (Index <= Length(Text)) and (Text[Index] > ' ') do
      Index := Index + 1;
    FileName := Copy(Text, 1, Index - 1);
    Params := TrimLeftWhitespace(Copy(Text, Index + 1, MaxInt));
  end;
end;

function ContainsSilentFlag(const Params: string): Boolean;
var
  UpperParams: string;
begin
  UpperParams := Uppercase(' ' + Params + ' ');
  Result :=
    (Pos(' /SILENT ', UpperParams) > 0) or
    (Pos(' /VERYSILENT ', UpperParams) > 0);
end;

procedure ResolveExistingUninstallCommand();
begin
  SplitCommandLine(ExistingUninstallString, ExistingUninstallExe, ExistingUninstallParams);
end;

function ExistingInstallSummary(): string;
begin
  Result :=
    'Face Manager is already installed on this computer.' + #13#10 + #13#10 +
    'Installed version: ' + ExistingInstallVersion + #13#10 +
    'Install location: ' + ExistingInstallDir + #13#10 +
    'Data folder: ' + AppDataDir() + #13#10 + #13#10 +
    'Choose whether to keep the current local data and only update the application, or perform a clean reinstall.';
end;

procedure InitializeWizard();
begin
  ExistingInstallDetected :=
    TryGetExistingInstallValue('UninstallString', ExistingUninstallString);

  if not ExistingInstallDetected then
    exit;

  if not TryGetExistingInstallValue('DisplayVersion', ExistingInstallVersion) then
    ExistingInstallVersion := 'Unknown';
  if not TryGetExistingInstallValue('InstallLocation', ExistingInstallDir) then
    ExistingInstallDir := ExpandConstant('{app}');
  TryGetExistingInstallValue('QuietUninstallString', ExistingUninstallString);
  ResolveExistingUninstallCommand();
  if ExistingInstallDir <> '' then
    WizardForm.DirEdit.Text := ExistingInstallDir;

  ExistingInstallPage :=
    CreateCustomPage(
      wpWelcome,
      'Update Existing Installation',
      'Choose how the installer should handle the current Face Manager installation'
    );

  ExistingInstallInfoLabel := TNewStaticText.Create(ExistingInstallPage);
  ExistingInstallInfoLabel.Parent := ExistingInstallPage.Surface;
  ExistingInstallInfoLabel.Left := 0;
  ExistingInstallInfoLabel.Top := 0;
  ExistingInstallInfoLabel.Width := ExistingInstallPage.SurfaceWidth;
  ExistingInstallInfoLabel.Height := ScaleY(96);
  ExistingInstallInfoLabel.AutoSize := False;
  ExistingInstallInfoLabel.WordWrap := True;
  ExistingInstallInfoLabel.Caption := ExistingInstallSummary();

  UpdateInstallRadio := TNewRadioButton.Create(ExistingInstallPage);
  UpdateInstallRadio.Parent := ExistingInstallPage.Surface;
  UpdateInstallRadio.Left := 0;
  UpdateInstallRadio.Top := ExistingInstallInfoLabel.Top + ExistingInstallInfoLabel.Height + ScaleY(8);
  UpdateInstallRadio.Width := ExistingInstallPage.SurfaceWidth;
  UpdateInstallRadio.Height := ScaleY(36);
  UpdateInstallRadio.Caption := 'Keep current data and update the application (recommended)';
  UpdateInstallRadio.Checked := True;

  ReplaceInstallRadio := TNewRadioButton.Create(ExistingInstallPage);
  ReplaceInstallRadio.Parent := ExistingInstallPage.Surface;
  ReplaceInstallRadio.Left := 0;
  ReplaceInstallRadio.Top := UpdateInstallRadio.Top + UpdateInstallRadio.Height + ScaleY(6);
  ReplaceInstallRadio.Width := ExistingInstallPage.SurfaceWidth;
  ReplaceInstallRadio.Height := ScaleY(36);
  ReplaceInstallRadio.Caption := 'Remove current local data and reinstall from scratch';
end;

procedure HandleExistingInstall();
var
  ResultCode: Integer;
  SilentParams: string;
begin
  if ExistingInstallHandled or not ExistingInstallDetected then
    exit;

  ExistingInstallHandled := True;

  if ExistingUninstallExe = '' then
  begin
    MsgBox(
      'The existing Face Manager uninstaller could not be resolved.' + #13#10 +
      'Please uninstall the current version manually and run the installer again.',
      mbCriticalError,
      MB_OK
    );
    Abort();
  end;

  SilentParams := ExistingUninstallParams;
  if not ContainsSilentFlag(SilentParams) then
  begin
    if SilentParams <> '' then
      SilentParams := SilentParams + ' ';
    SilentParams := SilentParams + '/VERYSILENT';
  end;
  SilentParams := Trim(SilentParams + ' /SUPPRESSMSGBOXES /NORESTART');

  Log(
    Format(
      'Running existing uninstaller: %s %s',
      [ExistingUninstallExe, SilentParams]
    )
  );

  if not Exec(
    ExistingUninstallExe,
    SilentParams,
    '',
    SW_HIDE,
    ewWaitUntilTerminated,
    ResultCode
  ) then
  begin
    MsgBox(
      'The existing Face Manager installation could not be updated automatically.' + #13#10 +
      'Please close the application and try again.',
      mbCriticalError,
      MB_OK
    );
    Abort();
  end;

  if ResultCode <> 0 then
  begin
    MsgBox(
      'The existing Face Manager installation returned an error during the update step.' + #13#10 +
      'Exit code: ' + IntToStr(ResultCode) + #13#10 +
      'Please close the application and try again.',
      mbCriticalError,
      MB_OK
    );
    Abort();
  end;

  if ReplaceInstallRadio.Checked and DirExists(AppDataDir()) then
    DelTree(AppDataDir(), True, True, True);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
    HandleExistingInstall();
end;
