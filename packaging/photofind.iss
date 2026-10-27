; Inno Setup script for Photo Finder (used by build_windows.bat).
; Installs for the current user only, so no administrator password is needed.

#define AppName "Photo Finder"
#define AppVersion "0.5.2"

[Setup]
AppId={{8B6C7A51-3E2D-4F7B-9C1A-6D5E4F3A2B10}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName}
DefaultDirName={localappdata}\Programs\Photo Finder
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir=..
OutputBaseFilename=PhotoFinderSetup
SetupIconFile=..\photofind-icon.ico
UninstallDisplayIcon={app}\PhotoFinder.exe
UninstallDisplayName={#AppName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "Put a shortcut on the desktop"

[Files]
Source: "..\build\dist\PhotoFinder\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\PhotoFinder.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\PhotoFinder.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\PhotoFinder.exe"; Description: "Open Photo Finder now"; Flags: nowait postinstall skipifsilent

; Your photos, index and names live in %LOCALAPPDATA%\photofind and are kept on uninstall,
; so reinstalling picks up where you left off.
