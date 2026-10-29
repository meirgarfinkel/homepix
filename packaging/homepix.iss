; Inno Setup script for HomePix (used by build_windows.bat).
; Installs for the current user only, so no administrator password is needed.

#define AppName "HomePix"
#define AppVersion "1.0.0"

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

[Tasks]
Name: "desktopicon"; Description: "Put a shortcut on the desktop"

[Files]
Source: "..\build\dist\HomePix\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\HomePix.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\HomePix.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\HomePix.exe"; Description: "Open HomePix now"; Flags: nowait postinstall skipifsilent

; Your photos, index and names live in %LOCALAPPDATA%\homepix and are kept on uninstall,
; so reinstalling picks up where you left off.
