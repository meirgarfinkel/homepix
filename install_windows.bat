@echo off
rem Installs Photo Finder for the current user and adds Start menu and desktop shortcuts.
rem Double-click this file in the folder you downloaded.
setlocal
set "SRC=%~dp0"
set "BASE=%LOCALAPPDATA%\photofind"
set "APP=%BASE%\app"
set "ENV=%BASE%\env"

echo Installing Photo Finder...

where py >nul 2>nul
if errorlevel 1 (
  echo.
  echo Photo Finder needs Python. Install it from https://www.python.org/downloads/
  echo then run this installer again.
  echo.
  pause
  exit /b 1
)

if not exist "%APP%" mkdir "%APP%"
if exist "%APP%\photofind.py" del /q "%APP%\photofind.py" "%APP%\photofind_ui.html"
if exist "%APP%\photofind" rmdir /s /q "%APP%\photofind"
xcopy /e /i /q /y "%SRC%photofind" "%APP%\photofind" >nul
for %%f in (photofind_app.py photofind-icon.png photofind-icon.ico requirements.txt) do (
  copy /y "%SRC%%%f" "%APP%\" >nul
)

if not exist "%ENV%\Scripts\python.exe" py -3 -m venv "%ENV%"
echo Downloading the parts Photo Finder needs (one time, about 100 MB)...
"%ENV%\Scripts\python.exe" -m pip install --quiet --upgrade pip
"%ENV%\Scripts\python.exe" -m pip install --quiet -r "%APP%\requirements.txt"
if errorlevel 1 (
  echo Something went wrong while downloading. Check your internet connection and run this again.
  pause
  exit /b 1
)

rem pythonw runs the app without a console window
powershell -NoProfile -Command ^
  "$s = New-Object -ComObject WScript.Shell;" ^
  "foreach ($dir in @([Environment]::GetFolderPath('Desktop'), (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'))) {" ^
  "  $l = $s.CreateShortcut((Join-Path $dir 'Photo Finder.lnk'));" ^
  "  $l.TargetPath = '%ENV%\Scripts\pythonw.exe';" ^
  "  $l.Arguments = '\"%APP%\photofind_app.py\"';" ^
  "  $l.IconLocation = '%APP%\photofind-icon.ico';" ^
  "  $l.WorkingDirectory = '%APP%';" ^
  "  $l.Save() }"

echo.
echo Done. Open Photo Finder from your desktop or the Start menu.
pause
