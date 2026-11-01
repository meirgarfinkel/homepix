; Inno Setup script for HomePix (used by build_windows.bat).
; Installs for the current user only, so no administrator password is needed.

#define AppName "HomePix"
#define AppVersion "1.0.1"

[Setup]
AppId={{8B6C7A51-3E2D-4F7B-9C1A-6D5E4F3A2B10}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName}
DefaultDirName={localappdata}\Programs\HomePix
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir=..
OutputBaseFilename=HomePixSetup
SetupIconFile=..\homepix-icon.ico
UninstallDisplayIcon={app}\HomePix.exe
UninstallDisplayName={#AppName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; tell Explorer about the file types below (Open with > HomePix)
ChangesAssociations=yes
; an update (Install updates in the app) closes the running HomePix first
CloseApplications=force

[Tasks]
Name: "desktopicon"; Description: "Put a shortcut on the desktop"

[Files]
Source: "..\build\dist\HomePix\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\HomePix.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\HomePix.exe"; Tasks: desktopicon

[Registry]
; "Open with > HomePix" in Explorer for photos and videos, and HomePix in Settings > Default apps.
; Only offered, never made the default: the user chooses that. All of it goes on uninstall.
Root: HKA; Subkey: "Software\Classes\HomePix.Photo"; ValueType: string; ValueData: "Photo or video"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\HomePix.Photo\DefaultIcon"; ValueType: string; ValueData: "{app}\HomePix.exe,0"
Root: HKA; Subkey: "Software\Classes\HomePix.Photo\shell\open\command"; ValueType: string; ValueData: """{app}\HomePix.exe"" ""%1"""
Root: HKA; Subkey: "Software\Classes\Applications\HomePix.exe"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "HomePix"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Applications\HomePix.exe\shell\open\command"; ValueType: string; ValueData: """{app}\HomePix.exe"" ""%1"""
Root: HKA; Subkey: "Software\HomePix\Capabilities"; ValueType: string; ValueName: "ApplicationName"; ValueData: "HomePix"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\HomePix\Capabilities"; ValueType: string; ValueName: "ApplicationDescription"; ValueData: "Browse, search and organize your photos"
Root: HKA; Subkey: "Software\RegisteredApplications"; ValueType: string; ValueName: "HomePix"; ValueData: "Software\HomePix\Capabilities"; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\.jpg\OpenWithProgids"; ValueType: string; ValueName: "HomePix.Photo"; ValueData: ""; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\Applications\HomePix.exe\SupportedTypes"; ValueType: string; ValueName: ".jpg"; ValueData: ""
Root: HKA; Subkey: "Software\HomePix\Capabilities\FileAssociations"; ValueType: string; ValueName: ".jpg"; ValueData: "HomePix.Photo"
Root: HKA; Subkey: "Software\Classes\.jpeg\OpenWithProgids"; ValueType: string; ValueName: "HomePix.Photo"; ValueData: ""; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\Applications\HomePix.exe\SupportedTypes"; ValueType: string; ValueName: ".jpeg"; ValueData: ""
Root: HKA; Subkey: "Software\HomePix\Capabilities\FileAssociations"; ValueType: string; ValueName: ".jpeg"; ValueData: "HomePix.Photo"
Root: HKA; Subkey: "Software\Classes\.png\OpenWithProgids"; ValueType: string; ValueName: "HomePix.Photo"; ValueData: ""; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\Applications\HomePix.exe\SupportedTypes"; ValueType: string; ValueName: ".png"; ValueData: ""
Root: HKA; Subkey: "Software\HomePix\Capabilities\FileAssociations"; ValueType: string; ValueName: ".png"; ValueData: "HomePix.Photo"
Root: HKA; Subkey: "Software\Classes\.webp\OpenWithProgids"; ValueType: string; ValueName: "HomePix.Photo"; ValueData: ""; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\Applications\HomePix.exe\SupportedTypes"; ValueType: string; ValueName: ".webp"; ValueData: ""
Root: HKA; Subkey: "Software\HomePix\Capabilities\FileAssociations"; ValueType: string; ValueName: ".webp"; ValueData: "HomePix.Photo"
Root: HKA; Subkey: "Software\Classes\.bmp\OpenWithProgids"; ValueType: string; ValueName: "HomePix.Photo"; ValueData: ""; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\Applications\HomePix.exe\SupportedTypes"; ValueType: string; ValueName: ".bmp"; ValueData: ""
Root: HKA; Subkey: "Software\HomePix\Capabilities\FileAssociations"; ValueType: string; ValueName: ".bmp"; ValueData: "HomePix.Photo"
Root: HKA; Subkey: "Software\Classes\.tif\OpenWithProgids"; ValueType: string; ValueName: "HomePix.Photo"; ValueData: ""; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\Applications\HomePix.exe\SupportedTypes"; ValueType: string; ValueName: ".tif"; ValueData: ""
Root: HKA; Subkey: "Software\HomePix\Capabilities\FileAssociations"; ValueType: string; ValueName: ".tif"; ValueData: "HomePix.Photo"
Root: HKA; Subkey: "Software\Classes\.tiff\OpenWithProgids"; ValueType: string; ValueName: "HomePix.Photo"; ValueData: ""; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\Applications\HomePix.exe\SupportedTypes"; ValueType: string; ValueName: ".tiff"; ValueData: ""
Root: HKA; Subkey: "Software\HomePix\Capabilities\FileAssociations"; ValueType: string; ValueName: ".tiff"; ValueData: "HomePix.Photo"
Root: HKA; Subkey: "Software\Classes\.gif\OpenWithProgids"; ValueType: string; ValueName: "HomePix.Photo"; ValueData: ""; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\Applications\HomePix.exe\SupportedTypes"; ValueType: string; ValueName: ".gif"; ValueData: ""
Root: HKA; Subkey: "Software\HomePix\Capabilities\FileAssociations"; ValueType: string; ValueName: ".gif"; ValueData: "HomePix.Photo"
Root: HKA; Subkey: "Software\Classes\.dng\OpenWithProgids"; ValueType: string; ValueName: "HomePix.Photo"; ValueData: ""; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\Applications\HomePix.exe\SupportedTypes"; ValueType: string; ValueName: ".dng"; ValueData: ""
Root: HKA; Subkey: "Software\HomePix\Capabilities\FileAssociations"; ValueType: string; ValueName: ".dng"; ValueData: "HomePix.Photo"
Root: HKA; Subkey: "Software\Classes\.heic\OpenWithProgids"; ValueType: string; ValueName: "HomePix.Photo"; ValueData: ""; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\Applications\HomePix.exe\SupportedTypes"; ValueType: string; ValueName: ".heic"; ValueData: ""
Root: HKA; Subkey: "Software\HomePix\Capabilities\FileAssociations"; ValueType: string; ValueName: ".heic"; ValueData: "HomePix.Photo"
Root: HKA; Subkey: "Software\Classes\.heif\OpenWithProgids"; ValueType: string; ValueName: "HomePix.Photo"; ValueData: ""; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\Applications\HomePix.exe\SupportedTypes"; ValueType: string; ValueName: ".heif"; ValueData: ""
Root: HKA; Subkey: "Software\HomePix\Capabilities\FileAssociations"; ValueType: string; ValueName: ".heif"; ValueData: "HomePix.Photo"
Root: HKA; Subkey: "Software\Classes\.mp4\OpenWithProgids"; ValueType: string; ValueName: "HomePix.Photo"; ValueData: ""; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\Applications\HomePix.exe\SupportedTypes"; ValueType: string; ValueName: ".mp4"; ValueData: ""
Root: HKA; Subkey: "Software\HomePix\Capabilities\FileAssociations"; ValueType: string; ValueName: ".mp4"; ValueData: "HomePix.Photo"
Root: HKA; Subkey: "Software\Classes\.mov\OpenWithProgids"; ValueType: string; ValueName: "HomePix.Photo"; ValueData: ""; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\Applications\HomePix.exe\SupportedTypes"; ValueType: string; ValueName: ".mov"; ValueData: ""
Root: HKA; Subkey: "Software\HomePix\Capabilities\FileAssociations"; ValueType: string; ValueName: ".mov"; ValueData: "HomePix.Photo"
Root: HKA; Subkey: "Software\Classes\.m4v\OpenWithProgids"; ValueType: string; ValueName: "HomePix.Photo"; ValueData: ""; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\Applications\HomePix.exe\SupportedTypes"; ValueType: string; ValueName: ".m4v"; ValueData: ""
Root: HKA; Subkey: "Software\HomePix\Capabilities\FileAssociations"; ValueType: string; ValueName: ".m4v"; ValueData: "HomePix.Photo"

[Run]
Filename: "{app}\HomePix.exe"; Description: "Open HomePix now"; Flags: nowait postinstall skipifsilent
; after an update from inside the app (setup /SILENT /relaunch=1), open HomePix again
Filename: "{app}\HomePix.exe"; Flags: nowait; Check: Relaunch


; Your photos, index and names live in %LOCALAPPDATA%\homepix and are kept on uninstall,
; so reinstalling picks up where you left off.

[Code]
function Relaunch: Boolean;
begin
  Result := ExpandConstant('{param:relaunch|0}') = '1';
end;
