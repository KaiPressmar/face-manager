#define MyAppName "Face Manager"
#define MyAppPublisher "Face Manager Project"
#define MyAppURL "https://github.com/KaiPressmar/face-manager"
#define MyAppExeName "FaceManager.exe"

[Setup]
AppId={{0E1A7413-4FD3-4A24-A59A-3A4B359EA5D0}
AppName={#MyAppName}
AppVersion={#AppVersion}
AppVerName={#MyAppName} {#AppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
AppComments=Local desktop application for organizing photos by recognized faces
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
OutputDir={#OutputDir}
OutputBaseFilename=FaceManager-Setup-{#AppVersion}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
SetupIconFile={#SourceDir}\packaging\windows\assets\face-manager-icon.ico
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} installer
VersionInfoOriginalFileName=FaceManager-Setup-{#AppVersion}.exe
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#AppVersion}
VersionInfoVersion={#AppVersion}
CloseApplications=yes
CloseApplicationsFilter={#MyAppExeName}
ChangesAssociations=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\build\FaceManagerLibraryBackup.exe"; Flags: dontcopy
Source: "{#SourceDir}\dist\FaceManager\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourceDir}\packaging\windows\assets\face-manager-icon.ico"; DestDir: "{app}"; DestName: "FaceManager-{#AppVersion}.ico"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\FaceManager-{#AppVersion}.ico"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; IconFilename: "{app}\FaceManager-{#AppVersion}.ico"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
const
  UninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{0E1A7413-4FD3-4A24-A59A-3A4B359EA5D0}_is1';
  MoveFileReplaceExisting = 1;
  MoveFileWriteThrough = 8;

var
  ExistingInstallPage: TWizardPage;
  ExistingInstallInfoLabel: TNewStaticText;
  UpdateInstallRadio: TNewRadioButton;
  ReplaceInstallRadio: TNewRadioButton;
  ExistingInstallDetected: Boolean;
  ExistingInstallVersion: string;
  ExistingInstallDir: string;
  ExistingUninstallString: string;
  ExistingUninstallExe: string;
  ExistingUninstallParams: string;
  PreviousExe: string;
  RollbackExe: string;
  InstallCompleted: Boolean;

function MoveFileEx(ExistingName, NewName: string; Flags: LongWord): Boolean;
  external 'MoveFileExW@kernel32.dll stdcall';

function AppDataDir(): string;
begin
  Result := GetEnv('FACE_MANAGER_DATA_DIR');
  if Result = '' then
    Result := ExpandConstant('{localappdata}\FaceManager');
end;

function InitializeSetup(): Boolean;
begin
  ExistingInstallDetected := RegKeyExists(HKCU64, UninstallKey);
  Result := ExistingInstallDetected or not RegKeyExists(HKLM64, UninstallKey);
  if not Result then
    SuppressibleMsgBox(
      'A machine-wide Face Manager installation was found. This per-user installer cannot update it.',
      mbCriticalError, MB_OK, IDOK
    );
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
  if not ExistingInstallDetected then
    exit;

  if not RegQueryStringValue(HKCU64, UninstallKey, 'DisplayVersion', ExistingInstallVersion) then
    ExistingInstallVersion := 'Unknown';
  if not RegQueryStringValue(HKCU64, UninstallKey, 'QuietUninstallString', ExistingUninstallString) then
    RegQueryStringValue(HKCU64, UninstallKey, 'UninstallString', ExistingUninstallString);
  SplitCommandLine(ExistingUninstallString, ExistingUninstallExe, ExistingUninstallParams);
  if not RegQueryStringValue(HKCU64, UninstallKey, 'InstallLocation', ExistingInstallDir) then
    ExistingInstallDir := ExtractFileDir(ExistingUninstallExe);
  ExistingInstallDir := RemoveBackslashUnlessRoot(ExistingInstallDir);
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
  ReplaceInstallRadio.Caption := 'Remove the default local library and reinstall from scratch';
  ReplaceInstallRadio.Enabled := GetEnv('FACE_MANAGER_DATA_DIR') = '';
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := ExistingInstallDetected and (PageID = wpSelectDir);
end;

procedure StopInstallation(const MessageText: string);
begin
  Log(MessageText);
  SuppressibleMsgBox(MessageText, mbCriticalError, MB_OK, IDOK);
  Abort();
end;

procedure BackUpLibrary();
var
  DatabasePath: string;
  ResultCode: Integer;
begin
  DatabasePath := AddBackslash(AppDataDir()) + 'database.sqlite';
  if (GetEnv('FACE_MANAGER_DATA_DIR') = '') and not FileExists(DatabasePath) then
    exit;

  ExtractTemporaryFile('FaceManagerLibraryBackup.exe');
  if not Exec(
    ExpandConstant('{tmp}\FaceManagerLibraryBackup.exe'), '"' + DatabasePath + '" "' + ExistingInstallVersion + '"',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode
  ) or (ResultCode <> 0) then
    StopInstallation(
      'The current library could not be backed up. Close Face Manager, check available disk space, and retry setup.'
    );
  Log('Saved a pre-update SQLite backup in ' + AddBackslash(AppDataDir()) + 'update-backups');
end;

procedure KeepPreviousExecutable();
var
  CandidateBackup: string;
begin
  if not ExistingInstallDetected then
    exit;

  if (ExistingInstallDir = '') or
     (CompareText(RemoveBackslashUnlessRoot(ExpandConstant('{app}')), ExistingInstallDir) <> 0) then
    StopInstallation('Setup could not resolve the current per-user Face Manager installation directory.');

  PreviousExe := AddBackslash(ExistingInstallDir) + '{#MyAppExeName}';
  if not FileExists(PreviousExe) then
    exit;

  CandidateBackup := AddBackslash(ExistingInstallDir) + 'FaceManager-rollback-' +
    GetDateTimeString('yyyymmddhhnnsszzz', '', '') + '.exe';
  if not FileCopy(PreviousExe, CandidateBackup, True) then
    StopInstallation('Setup could not save the current executable for recovery. Check available disk space and retry.');
  RollbackExe := CandidateBackup;
  Log('Saved the prior executable for recovery at ' + RollbackExe);
end;

procedure HandleCleanInstall();
var
  ResultCode: Integer;
  SilentParams: string;
begin
  if (ExistingUninstallExe = '') or (ExistingInstallDir = '') or
     (CompareText(ExtractFileDir(ExistingUninstallExe), ExistingInstallDir) <> 0) or
     (Pos('UNINS', Uppercase(ExtractFileName(ExistingUninstallExe))) <> 1) or
     (CompareText(ExtractFileExt(ExistingUninstallExe), '.exe') <> 0) then
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

  Log('Running existing uninstaller: ' + ExistingUninstallExe + ' ' + SilentParams);

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

  if DirExists(AppDataDir()) and not DelTree(AppDataDir(), True, True, True) then
    StopInstallation('Setup could not remove the default local library. Close Face Manager and retry setup.');
end;

function IsRuntimeVersion(const Version: string): Boolean;
var
  Index: Integer;
  Separators: Integer;
begin
  Result := False;
  Separators := 0;
  for Index := 1 to Length(Version) do
  begin
    if Version[Index] = '.' then
      Separators := Separators + 1
    else if (Version[Index] < '0') or (Version[Index] > '9') then
      exit;
  end;
  Result := Separators = 2;
end;

procedure RemoveUnusedRuntime();
var
  PreviousRuntime: string;
begin
  if not ExistingInstallDetected or (ExistingInstallDir = '') then
    exit;
  { A configured data root can be inside the old runtime directory. }
  if GetEnv('FACE_MANAGER_DATA_DIR') <> '' then
    exit;

  PreviousRuntime := AddBackslash(ExistingInstallDir) + '_internal';
  if DirExists(PreviousRuntime) and not DelTree(PreviousRuntime, True, True, True) then
    Log('Could not remove unused legacy runtime at ' + PreviousRuntime);

  if IsRuntimeVersion(ExistingInstallVersion) and (ExistingInstallVersion <> '{#AppVersion}') then
  begin
    PreviousRuntime := AddBackslash(ExistingInstallDir) + '_internal-' + ExistingInstallVersion;
    if DirExists(PreviousRuntime) and not DelTree(PreviousRuntime, True, True, True) then
      Log('Could not remove unused runtime at ' + PreviousRuntime);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
  begin
    if ExistingInstallDetected then
    begin
      if ReplaceInstallRadio.Checked then
      begin
        HandleCleanInstall();
        exit;
      end;
    end;
    BackUpLibrary();
    KeepPreviousExecutable();
  end;
  if CurStep = ssDone then
  begin
    InstallCompleted := True;
    RemoveUnusedRuntime();
  end;
end;

procedure DeinitializeSetup();
begin
  if RollbackExe = '' then
    exit;

  if InstallCompleted then
    DeleteFile(RollbackExe)
  else if not MoveFileEx(RollbackExe, PreviousExe, MoveFileReplaceExisting or MoveFileWriteThrough) then
    SuppressibleMsgBox(
      'Setup could not restore the previous executable. Close Face Manager and use the recovery copy at:' +
      #13#10 + RollbackExe,
      mbCriticalError, MB_OK, IDOK
    );
end;
