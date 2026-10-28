@echo off
rem Builds PhotoFinderSetup.exe: a normal Windows installer people double-click.
rem Run from a Command Prompt in the photofind folder:   packaging\build_windows.bat
rem Needs Python 3.10+ (python.org) and Inno Setup 6 (jrsoftware.org/isinfo.php).
setlocal
cd /d "%~dp0\.."

set "PY=py -3"
where py >nul 2>nul || set "PY=python"

echo 1/5  Setting up a clean Python environment for building...
if not exist build\env\Scripts\python.exe %PY% -m venv build\env || goto fail
build\env\Scripts\python -m pip install --quiet --upgrade pip || goto fail
build\env\Scripts\python -m pip install --quiet -r requirements.txt pyinstaller || goto fail

echo 2/5  Bundling Photo Finder with PyInstaller...
if exist build\dist rmdir /s /q build\dist
build\env\Scripts\pyinstaller --noconfirm --clean --log-level ERROR --distpath build\dist --workpath build\work packaging\photofind.spec || goto fail

echo 3/5  Adding ExifTool with its own Perl...
powershell -NoProfile -ExecutionPolicy Bypass -File packaging\bundle_exiftool.ps1 build\exiftool || goto fail
xcopy /e /i /q /y build\exiftool build\dist\PhotoFinder\_internal\exiftool >nul || goto fail

echo 4/5  Checking that the bundled app starts...
rem (with a data folder of its own, so it never touches yours; AI models put in build\selftest\models
rem are checked too)
if exist build\selftest\app.log del build\selftest\app.log
set "PHOTOFIND_HOME=%CD%\build\selftest"
start /wait "" build\dist\PhotoFinder\PhotoFinder.exe --self-test
if errorlevel 1 (
  set "PHOTOFIND_HOME="
  type build\selftest\app.log
  echo The self-test failed: see above.
  goto fail
)
set "PHOTOFIND_HOME="
type build\selftest\app.log

echo 5/5  Making the installer...
set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (
  echo Inno Setup 6 isn't installed, so no installer was made.
  echo The app itself is ready in build\dist\PhotoFinder\ ^(PhotoFinder.exe^).
  echo Install Inno Setup from https://jrsoftware.org/isinfo.php and run this again for the installer.
  exit /b 0
)
"%ISCC%" /Q packaging\photofind.iss || goto fail

echo.
echo Done: PhotoFinderSetup.exe
exit /b 0

:fail
echo.
echo The build stopped because of the error above.
exit /b 1
