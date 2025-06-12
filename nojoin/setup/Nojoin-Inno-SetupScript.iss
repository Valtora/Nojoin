; Nojoin Application Installer Script
; Located in: /setup/Nojoin-Inno-SetupScript.iss
; Created with Inno Setup 6.x
; Simplified version - installs files only, dependencies handled by setup_windows.bat

#define MyAppName "Nojoin"
#define MyAppVersion "0.6.2"
#define MyAppPublisher "Valtora"
#define MyAppURL "https://www.nojoin.co.uk"
#define MyAppExeName "Nojoin.py"
#define MyAppDescription "Audio processing and search application"

[Setup]
; Unique identifier for this application
AppId={{87B5F3E3-AEAE-475B-9E1E-18105CECF8CE}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
AppCopyright=Copyright (C) 2024 {#MyAppPublisher}
AppComments={#MyAppDescription}

; Installation directories - dynamic based on privileges
DefaultDirName={code:GetDefaultDirName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
DisableDirPage=no
DisableProgramGroupPage=no

; License and documentation
LicenseFile=..\..\LICENSE

; Security and privileges - user installs only, no admin prompt
PrivilegesRequired=lowest

; Output configuration
OutputDir=dist
OutputBaseFilename={#MyAppName}-Setup-{#MyAppVersion}

; Installation modes
UsedUserAreasWarning=no
SetupIconFile=..\..\assets\favicon.ico
UninstallDisplayIcon={app}\assets\favicon.ico

; Compression and appearance
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
WizardSizePercent=120

; Version information
VersionInfoVersion={#MyAppVersion}
VersionInfoDescription={#MyAppDescription}
VersionInfoCopyright=Copyright (C) 2024 {#MyAppPublisher}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "quicklaunchicon"; Description: "{cm:CreateQuickLaunchIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked; OnlyBelowVersion: 0
Name: "runsetup"; Description: "Run dependency setup after installation completes"; GroupDescription: "Post-installation setup:"

[Files]
; Main application files
Source: "..\..\Nojoin.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\requirements.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\README.md"; DestDir: "{app}"; Flags: ignoreversion

; Setup scripts
Source: "setup_windows.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\Start Nojoin.bat"; DestDir: "{app}"; Flags: ignoreversion

; Application directories with proper structure preservation
Source: "..\..\assets\*"; DestDir: "{app}\assets"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\..\models\*"; DestDir: "{app}\models"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist
; Exclude sensitive/generated files: config.json (contains API keys), database files, logs, cache, and setup directory
Source: "..\..\nojoin\*"; DestDir: "{app}\nojoin"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "config.json,nojoin_data.db,*.log,__pycache__\*,setup\*"

; Documentation (optional)
Source: "..\..\docs\*"; DestDir: "{app}\docs"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist

; NOTE: recordings directory is intentionally NOT included - it should start empty

[Dirs]
; Create directories that are needed by the application but should start empty
Name: "{app}\recordings"; Permissions: users-full
Name: "{app}\temp"; Permissions: users-full
Name: "{userappdata}\{#MyAppName}"; Permissions: users-full
Name: "{userappdata}\{#MyAppName}\backups"; Permissions: users-full

[Icons]
; Start Menu icons
Name: "{group}\{#MyAppName} Setup"; Filename: "{app}\setup_windows.bat"; WorkingDir: "{app}"; IconFilename: "{app}\assets\favicon.ico"; Comment: "Set up {#MyAppDescription} dependencies"
Name: "{group}\{#MyAppName}"; Filename: "{app}\Start Nojoin.bat"; WorkingDir: "{app}"; IconFilename: "{app}\assets\favicon.ico"; Comment: "{#MyAppDescription}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{group}\Visit GitHub Page"; Filename: "https://github.com/Valtora/Nojoin"; Comment: "Open {#MyAppName} documentation and support page"

; Desktop icon (optional)
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\Start Nojoin.bat"; WorkingDir: "{app}"; IconFilename: "{app}\assets\favicon.ico"; Comment: "Launch {#MyAppDescription}"; Tasks: desktopicon

; Quick Launch icon (optional, legacy)
Name: "{userappdata}\Microsoft\Internet Explorer\Quick Launch\{#MyAppName}"; Filename: "{app}\Start Nojoin.bat"; WorkingDir: "{app}"; Tasks: quicklaunchicon

[Registry]
; Application registration - uses HKA which automatically selects the appropriate hive
Root: HKA; Subkey: "SOFTWARE\{#MyAppPublisher}\{#MyAppName}"; ValueType: string; ValueName: "InstallPath"; ValueData: "{app}"; Flags: uninsdeletekey
Root: HKA; Subkey: "SOFTWARE\{#MyAppPublisher}\{#MyAppName}"; ValueType: string; ValueName: "Version"; ValueData: "{#MyAppVersion}"; Flags: uninsdeletekey

[Run]
; Post-installation tasks - run setup script to install dependencies
Filename: "{app}\setup_windows.bat"; WorkingDir: "{app}"; Description: "Set up Python environment and dependencies"; Flags: postinstall nowait skipifsilent; Tasks: runsetup

[UninstallDelete]
; Clean up files that are created at runtime
Type: filesandordirs; Name: "{app}\temp"
Type: filesandordirs; Name: "{app}\recordings"
Type: filesandordirs; Name: "{app}\.venv"
Type: filesandordirs; Name: "{app}\__pycache__"
Type: filesandordirs; Name: "{app}\nojoin\__pycache__"
Type: filesandordirs; Name: "{app}\nojoin\config.json"
Type: filesandordirs; Name: "{app}\nojoin\nojoin_data.db"
Type: filesandordirs; Name: "{app}\*.pyc"
Type: filesandordirs; Name: "{app}\*.pyo"
Type: filesandordirs; Name: "{app}\*.log"
Type: filesandordirs; Name: "{app}\nojoin\*.log"
Type: filesandordirs; Name: "{app}\run_nojoin.bat"
Type: filesandordirs; Name: "{app}\update_nojoin.bat"

[Messages]
SetupAppTitle=Setup - {#MyAppName}
SetupWindowTitle=Setup - {#MyAppName} {#MyAppVersion}
WelcomeLabel1=Welcome to the [name] Setup Wizard
BeveledLabel={#MyAppName} - Meeting Recording and Transcription

[Code]
// Simplified setup - just handle directory selection and existing installation detection

function GetUninstallString(): String;
var
  sUnInstPath: String;
  sUnInstallString: String;
begin
  sUnInstPath := ExpandConstant('Software\Microsoft\Windows\CurrentVersion\Uninstall\{#emit SetupSetting("AppId")}_is1');
  sUnInstallString := '';
  if not RegQueryStringValue(HKLM, sUnInstPath, 'UninstallString', sUnInstallString) then
    RegQueryStringValue(HKCU, sUnInstPath, 'UninstallString', sUnInstallString);
  Result := sUnInstallString;
end;

function IsUpgrade(): Boolean;
begin
  Result := (GetUninstallString() <> '');
end;

function InitializeSetup(): Boolean;
var
  V: Integer;
  iResultCode: Integer;
  sUnInstallString: String;
  CurrentVersion: String;
  NewVersion: String;
begin
  Result := True; // Default to continuing installation
  
  // Get current and new versions
  NewVersion := '{#MyAppVersion}';
  
  // Check if there's an existing installation
  if RegValueExists(HKEY_CURRENT_USER, 'Software\{#MyAppPublisher}\{#MyAppName}', 'Version') then
  begin
    // This is an upgrade
    RegQueryStringValue(HKEY_CURRENT_USER, 'Software\{#MyAppPublisher}\{#MyAppName}', 'Version', CurrentVersion);
    Log('Upgrading from version ' + CurrentVersion + ' to ' + NewVersion);
    
    // For silent installs (from built-in updater), proceed without prompts
    if WizardSilent then
    begin
      Log('Silent upgrade mode - proceeding automatically');
      Result := True;
    end
    else
    begin
      // Show upgrade confirmation for manual installs
      V := MsgBox(ExpandConstant('Nojoin ' + CurrentVersion + ' is currently installed. ' +
                  'This will upgrade it to version ' + NewVersion + '. ' +
                  'Your settings and data will be preserved. Continue?'), 
                  mbInformation, MB_YESNO);
      if V = IDNO then
        Result := False;
    end;
  end
  else
  begin
    // Fresh installation
    Log('Fresh installation of version ' + NewVersion);
  end;
end;

function GetDefaultDirName(Param: String): String;
begin
  // Always install to user directory to avoid permission issues with setup script
  Result := ExpandConstant('{localappdata}\{#MyAppName}');
end;

procedure InitializeWizard();
begin
  // Set welcome message
  WizardForm.WelcomeLabel2.Caption := 
    'This will install {#MyAppName} {#MyAppVersion} on your computer.' + #13#10#13#10 +
    'After the files are installed, you will need to run the dependency setup ' +
    'script to install Python, ffmpeg, and other required components.' + #13#10#13#10 +
    'Click Next to continue, or Cancel to exit Setup.';
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  // Update directory page caption 
  if CurPageID = wpSelectDir then
  begin
    WizardForm.DirEdit.Text := ExpandConstant('{localappdata}\{#MyAppName}');
    WizardForm.SelectDirLabel.Caption := 
      'Setup will install {#MyAppName} in the following folder for the current user.' + #13#10#13#10 +
      'This ensures the setup script can run without administrator privileges.' + #13#10#13#10 +
      'To continue, click Next. If you would like to select a different folder, click Browse.';
  end;
  
  // Update finish page to mention the setup script
  if CurPageID = wpFinished then
  begin
    WizardForm.FinishedLabel.Caption := 
      'Setup has finished installing {#MyAppName} on your computer.' + #13#10#13#10 +
      'IMPORTANT: You still need to run the dependency setup to install ' +
      'Python, ffmpeg, and other required components.' + #13#10#13#10 +
      'The setup script will run automatically if you selected that option, ' +
      'or you can run "setup_windows.bat" manually from the installation folder.' + #13#10#13#10 +
      'Click Finish to complete Setup.';
  end;
end;

