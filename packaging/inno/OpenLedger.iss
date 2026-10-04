; All paths and versions are supplied by the checked build script.
#ifndef BundleDir
  #error BundleDir is required
#endif
#ifndef OutputDir
  #error OutputDir is required
#endif
#ifndef AppVersion
  #error AppVersion is required
#endif
#ifndef WindowsVersion
  #error WindowsVersion is required
#endif
#define FixedAppId "2EA4E61C-9182-4B6C-BFF8-995365131679"

[Setup]
AppId={{2EA4E61C-9182-4B6C-BFF8-995365131679}
AppName=OpenLedger
AppVersion={#AppVersion}
AppPublisher=OpenLedger contributors
DefaultDirName={localappdata}\Programs\OpenLedger
DefaultGroupName=OpenLedger
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible and not arm64
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir={#OutputDir}
OutputBaseFilename=OpenLedger-{#AppVersion}-windows-x64-setup
VersionInfoVersion={#WindowsVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\OpenLedger.exe
CloseApplications=no
RestartApplications=no
LicenseFile={#BundleDir}\LICENSE
InfoBeforeFile=install-note.zh_CN.txt
SetupLogging=yes

[Languages]
Name: "chinesesimplified"; MessagesFile: "languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; Flags: unchecked

[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\OpenLedger"; Filename: "{app}\OpenLedger.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\OpenLedger"; Filename: "{app}\OpenLedger.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\OpenLedger.exe"; Description: "{cm:LaunchProgram,OpenLedger}"; Flags: nowait postinstall skipifsilent

[Code]
function GetFileAttributesW(Name: String): LongWord;
external 'GetFileAttributesW@kernel32.dll stdcall';
function CreateMutexW(Security: Integer; Own: Boolean; Name: String): Integer;
external 'CreateMutexW@kernel32.dll stdcall';
function CloseHandle(Handle: Integer): Boolean;
external 'CloseHandle@kernel32.dll stdcall';
function GetLastError(): LongWord;
external 'GetLastError@kernel32.dll stdcall';
function CreateFileW(Name: String; Access, Share: LongWord; Security: Integer;
  Disposition, Attributes: LongWord; Template: Integer): Integer;
external 'CreateFileW@kernel32.dll stdcall';

var InstallationHandle: Integer;

procedure ReleaseInstallationGate();
begin
  if InstallationHandle <> 0 then begin
    CloseHandle(InstallationHandle);
    InstallationHandle := 0;
  end;
end;

function BeginInstallation(): Boolean;
var Error: LongWord;
begin
  InstallationHandle := CreateMutexW(0, False, 'Local\OpenLedger.InstallationInProgress');
  Error := GetLastError();
  Result := (InstallationHandle <> 0) and (Error <> 183);
  if not Result then ReleaseInstallationGate();
end;

function ProgramFileAvailable(FileName: String): Boolean;
var Handle: Integer;
begin
  Result := True;
  if not FileExists(FileName) then exit;
  Handle := CreateFileW(FileName, $40000000, 0, 0, 3, $80, 0);
  Result := Handle <> -1;
  if Result then CloseHandle(Handle);
end;

function ApplicationIsRunning(): Boolean;
begin
  Result := CheckForMutexes('Local\OpenLedger.InstallationGate');
end;

function InitializeSetup(): Boolean;
begin
  Result := BeginInstallation() and (not ApplicationIsRunning());
  if not Result then
    SuppressibleMsgBox('请先从 OpenLedger 的“退出”菜单退出所有实例，再安装。 / Quit all OpenLedger instances before installing.', mbError, MB_OK, IDOK);
end;

function InitializeUninstall(): Boolean;
begin
  Result := BeginInstallation() and (not ApplicationIsRunning()) and
    ProgramFileAvailable(ExpandConstant('{app}\OpenLedger.exe'));
  if not Result then
    SuppressibleMsgBox('请先退出 OpenLedger，再卸载；账目将保留。 / Quit OpenLedger before uninstalling. Your data will be retained.', mbError, MB_OK, IDOK);
end;

procedure DeinitializeSetup();
begin
  ReleaseInstallationGate();
end;

procedure DeinitializeUninstall();
begin
  ReleaseInstallationGate();
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then ReleaseInstallationGate();
end;

function ValidateInstallDirectory(): String;
var
  Target, DataRoot, OldVersion, Ancestor, Parent: String;
  Installed, Incoming: Int64;
  Found: TFindRec;
  Attributes: LongWord;
begin
  Result := '';
  Target := AddBackslash(ExpandFileName(WizardDirValue));
  DataRoot := AddBackslash(ExpandConstant('{localappdata}\OpenLedger'));
  Ancestor := RemoveBackslashUnlessRoot(Target);
  repeat
    Attributes := GetFileAttributesW(Ancestor);
    if (Attributes <> $FFFFFFFF) and ((Attributes and $400) <> 0) then begin
      Result := '程序目录不能经过目录联接或符号链接。 / Installation paths cannot pass through reparse points.';
      exit;
    end;
    Parent := ExtractFileDir(Ancestor);
    if CompareText(Parent, Ancestor) = 0 then break;
    Ancestor := Parent;
  until Ancestor = '';
  if CompareText(Target, AddBackslash(ExtractFileDrive(Target))) = 0 then begin
    Result := '请选择独立程序文件夹。 / Select a dedicated application folder.';
    exit;
  end;
  if (CompareText(Target, DataRoot) = 0) or
     (Pos(Lowercase(Target), Lowercase(DataRoot)) = 1) or
     (Pos(Lowercase(DataRoot), Lowercase(Target)) = 1) or
     FileExists(Target + 'database\openledger.sqlite3') then begin
    Result := '请选择独立程序目录，不能安装到账本目录或其上层目录。 / Select a separate program directory.';
    exit;
  end;
  if FileExists(Target + '.openledger-install.ini') then begin
    if GetIniString('OpenLedger', 'AppId', '', Target + '.openledger-install.ini') <> '{#FixedAppId}' then begin
      Result := '此目录属于其他安装。 / This directory belongs to another installation.';
      exit;
    end;
    OldVersion := GetIniString('OpenLedger', 'WindowsVersion', '', Target + '.openledger-install.ini');
    if (not StrToVersion(OldVersion, Installed)) or (not StrToVersion('{#WindowsVersion}', Incoming)) then begin
      Result := '已有安装的版本记录无效。 / Invalid installed version record.';
      exit;
    end;
    if ComparePackedVersion(Installed, Incoming) > 0 then begin
      Result := '不能覆盖较新的版本；回退请使用独立目录与已验证备份。 / Downgrades require a separate directory.';
    end;
  end else if FindFirst(Target + '*', Found) then begin
    try
      repeat
        if (Found.Name <> '.') and (Found.Name <> '..') then begin
          Result := '请选择空目录或已有 OpenLedger 安装目录。 / Select an empty directory or an OpenLedger installation.';
          break;
        end;
      until not FindNext(Found);
    finally
      FindClose(Found);
    end;
  end;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var Error: String;
begin
  Result := True;
  if CurPageID <> wpSelectDir then exit;
  Error := ValidateInstallDirectory();
  Result := Error = '';
  if not Result then SuppressibleMsgBox(Error, mbError, MB_OK, IDOK);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := ValidateInstallDirectory();
  if ApplicationIsRunning() then
    Result := '请先退出所有 OpenLedger 实例。 / Quit all OpenLedger instances first.';
  if not ProgramFileAvailable(ExpandConstant('{app}\OpenLedger.exe')) then
    Result := '程序文件正在使用或无法替换，请先退出旧版本。 / Quit the previous version before replacing program files.';
end;
