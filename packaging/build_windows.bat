@echo off
rem Builds HomePixSetup.exe: a normal Windows installer people double-click.
rem Run from a Command Prompt in the homepix folder:   packaging\build_windows.bat
rem Needs Python 3.10+ (python.org) and Inno Setup 6 (jrsoftware.org/isinfo.php).
setlocal
cd /d "%~dp0\.."

rem Python 3.12 if it's there (the AI libraries don't support the newest Python yet), else "python"
set "PY=python"
py -3.12 -c "" >nul 2>nul && set "PY=py -3.12"

echo 1/5  Setting up a clean Python environment for building...
if not exist build\env\Scripts\python.exe %PY% -m venv build\env || goto fail
build\env\Scripts\python -m pip install --quiet --upgrade pip || goto fail
build\env\Scripts\python -m pip install --quiet -r requirements.txt pyinstaller || goto fail

echo 2/5  Bundling HomePix with PyInstaller...
if exist build\dist rmdir /s /q build\dist
build\env\Scripts\pyinstaller --noconfirm --clean --log-level ERROR --distpath build\dist --workpath build\work packaging\homepix.spec || goto fail

echo 3/5  Adding ExifTool with its own Perl...
powershell -NoProfile -ExecutionPolicy Bypass -File packaging\bundle_exiftool.ps1 build\exiftool || goto fail
xcopy /e /i /q /y build\exiftool build\dist\HomePix\_internal\exiftool >nul || goto fail

echo 4/5  Checking that the bundled app starts...
rem (with a data folder of its own, so it never touches yours; AI models put in build\selftest\models
rem are checked too)
if exist build\selftest\app.log del build\selftest\app.log
set "HOMEPIX_HOME=%CD%\build\selftest"
start /wait "" build\dist\HomePix\HomePix.exe --self-test
if errorlevel 1 (
  set "HOMEPIX_HOME="
  type build\selftest\app.log
  echo The self-test failed: see above.
  goto fail
)
set "HOMEPIX_HOME="
type build\selftest\app.log

echo 5/5  Making the installer...
set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (
  echo Inno Setup 6 isn't installed, so no installer was made.
  echo The app itself is ready in build\dist\HomePix\ ^(HomePix.exe^).
  echo Install Inno Setup from https://jrsoftware.org/isinfo.php and run this again for the installer.
  exit /b 0
)
"%ISCC%" /Q packaging\homepix.iss || goto fail

echo.
echo Done: HomePixSetup.exe
exit /b 0

:fail
echo.
echo The build stopped because of the error above.
exit /b 1
