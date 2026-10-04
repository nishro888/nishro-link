; Nishro Link - the Windows setup wizard (Inno Setup 6).
;
;   Built by link\packaging\build-windows.ps1, which passes the version:
;   ISCC /DAppVersion=0.10.0 link\packaging\windows\NishroLink.iss
;
; One wizard, one permission prompt. It installs the program to Program Files,
; then has the program itself set up the service, the firewall and the
; settings (NishroLink.exe --install-service, see wininstall.py) - the same
; code that repairs an earlier install. It appears in Apps & features with its
; icon, version and publisher, and uninstalls cleanly from there.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6D3B5E2A-4C1F-4B8E-9E2D-5A7F1C3B8D40}
AppName=Nishro Link
AppVersion={#AppVersion}
AppVerName=Nishro Link {#AppVersion}
AppPublisher=nishro888
AppPublisherURL=https://github.com/nishro888/nishro-link
AppSupportURL=https://github.com/nishro888/nishro-link/issues
AppUpdatesURL=https://github.com/nishro888/nishro-link/releases
AppCopyright=Copyright (c) 2026 nishro888
VersionInfoVersion={#AppVersion}
VersionInfoProductName=Nishro Link
VersionInfoDescription=Nishro Link Setup
DefaultDirName={autopf}\Nishro Link
DisableProgramGroupPage=yes
LicenseFile=..\..\..\LICENSE
OutputDir=..\..\..\dist
OutputBaseFilename=NishroLink-Setup-{#AppVersion}
SetupIconFile=..\assets\nishro-link.ico
UninstallDisplayIcon={app}\NishroLink.exe
UninstallDisplayName=Nishro Link
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=no
UsedUserAreasWarning=no
MinVersion=10.0

[Messages]
WelcomeLabel2=This will install Nishro Link {#AppVersion} - one mouse and keyboard across your computers.%n%nIt runs in the background from the moment Windows starts, so another computer's mouse and keyboard also work on the lock and sign-in screens.

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[InstallDelete]
; What an earlier version left beside the exe: replaced whole, never mixed.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
; The program is a folder: NishroLink.exe and the files it runs from.
Source: "..\..\..\dist\NishroLink\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Nishro Link"; Filename: "{app}\NishroLink.exe"; Comment: "One mouse and keyboard across your computers"
Name: "{autodesktop}\Nishro Link"; Filename: "{app}\NishroLink.exe"; Tasks: desktopicon
; The tray icon, for everyone, at sign-in (tray.py).
Name: "{commonstartup}\Nishro Link tray"; Filename: "{app}\NishroLink.exe"; Parameters: "--tray"; Comment: "Nishro Link's everyday controls in the notification area"

[Run]
Filename:"{app}\NishroLink.exe"; Description: "Open Nishro Link"; Flags: postinstall nowait skipifsilent runasoriginaluser

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  // The service and any open window hold NishroLink.exe: stop them so it can
  // be replaced. (A service stuck starting is stopped the hard way.)
  Exec(ExpandConstant('{sys}\sc.exe'), 'stop NishroLink', '', SW_HIDE,
       ewWaitUntilTerminated, Code);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM NishroLink.exe', '', SW_HIDE,
       ewWaitUntilTerminated, Code);
  Sleep(800);
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Code: Integer;
  Args: String;
begin
  if CurStep = ssPostInstall then
  begin
    WizardForm.StatusLabel.Caption := 'Starting Nishro Link in the background...';
    Args := '--install-service --from-config "' +
            ExpandConstant('{userappdata}\NishroLink\config.json') + '"';
    if not Exec(ExpandConstant('{app}\NishroLink.exe'), Args, '', SW_HIDE,
                ewWaitUntilTerminated, Code) or (Code <> 0) then
      // (No line may start with "#": the preprocessor would take it.)
      MsgBox('Nishro Link is installed, but its background service did not ' +
             'start.' + #13#10#13#10 + 'Details are in ' +
             ExpandConstant('{commonappdata}\NishroLink\install.log'),
             mbError, MB_OK);
    // The tray icon at once, for whoever installed - not only from their next
    // sign-in. Started here, after the service, not as a [Run] entry: those
    // run BEFORE this step, and installing the service stops every other
    // NishroLink.exe - the new tray included.
    ExecAsOriginalUser(ExpandConstant('{app}\NishroLink.exe'), '--tray', '',
                       SW_SHOWNORMAL, ewNoWait, Code);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Code: Integer;
  Args: String;
begin
  if CurUninstallStep = usUninstall then
  begin
    Args := '--uninstall-service';
    if not UninstallSilent then
      if MsgBox('Also remove your Nishro Link settings and pairing?',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        Args := Args + ' --purge';
    Exec(ExpandConstant('{app}\NishroLink.exe'), Args, '', SW_HIDE,
         ewWaitUntilTerminated, Code);
  end;
end;
