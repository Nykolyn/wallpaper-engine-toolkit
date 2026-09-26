@echo off
:: Build a standalone WallpaperEngineToolkit.exe with PyInstaller.
cd /d "%~dp0"

:: Since 3.0.0 a build keeps its data in %LOCALAPPDATA%\WallpaperEngineToolkit,
:: outside dist\ altogether. A copy installed before then still has it in
:: dist\WallpaperEngineToolkit\data - the authors database, the DPAPI-encrypted
:: Steam key, the Rotator's history - until the new build's first start moves
:: it. PyInstaller empties its output folder before writing, so it never writes
:: there. It builds into build\stage, and robocopy mirrors the result into dist\
:: with that one data folder excluded - excluded from the copy AND from the
:: purge, so nothing under it can be deleted or overwritten by a build, failed
:: or not. (The old way stashed data\ in %TEMP% and put it back; a build that
:: died half way through the wipe took secrets.json with it.)
set "STAGE=build\stage"
set "APP=WallpaperEngineToolkit"
set "LIVE=%~dp0dist\%APP%"

:: A running copy holds its exe open, and robocopy cannot replace it.
tasklist /FI "IMAGENAME eq %APP%.exe" 2>nul | find /I "%APP%.exe" >nul
if not errorlevel 1 (
    echo %APP%.exe is running - the tray tracker, or a window.
    echo Quit it from the tray icon, or: schtasks /end /tn WallpaperEngineToolkitTracker
    echo Then run this again. Nothing was built.
    pause
    exit /b 1
)

echo Building %APP%.exe ...
if exist ".venv\Scripts\pyinstaller.exe" (
    set "PYI=.venv\Scripts\pyinstaller.exe"
) else (
    set "PYI=pyinstaller"
)
if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
)

:: The exe's Details tab carries the version from app\__init__.py.
%PY% tools\release.py version-file build\version_info.txt
if errorlevel 1 (
    echo BUILD FAILED - could not write the version resource.
    pause
    exit /b 1
)

:: This file must stay CRLF and plain ASCII. cmd.exe reads it in the OEM code
:: page, and a caret continuation followed by a bare LF does not continue - the
:: command below then runs with no program name and exits 9009.
::
:: --icon sets what Explorer and the taskbar show; --add-data ships the same
:: file so QIcon can paint the title bar and the Alt-Tab entry at runtime.
::
:: Nothing may be commented out INSIDE the command below: a caret continues the
:: line, and "::" on a continued line is handed to PyInstaller as an argument
:: rather than skipped. That went unnoticed for four days - every build died on
:: "Script file '::' does not exist" while the check at the end still printed
:: SUCCESS, because it only asked whether an exe existed, not whether this run
:: had made one. Both halves of that are fixed: comments live up here, and the
:: exit code is what decides.
::
:: PyInstaller writes a .spec file on every run - a copy of the arguments
:: below. --specpath puts it in build\ with the rest of the scratch, so a build
:: leaves the source tree as it found it. There is deliberately no spec file in
:: the repository: "pyinstaller <spec>" builds straight into dist\ and empties
:: dist\%APP% first, data\ included. The paths are absolute so they mean the
:: same thing wherever the spec file lands.
%PYI% ^
  --noconfirm ^
  --windowed ^
  --name "%APP%" ^
  --specpath "%~dp0build" ^
  --distpath "%STAGE%" ^
  --icon "%~dp0assets\icon.ico" ^
  --version-file "%~dp0build\version_info.txt" ^
  --add-data "%~dp0assets\icon.ico;assets" ^
  --collect-submodules app ^
  --collect-all imageio_ffmpeg ^
  "%~dp0run_app.py"
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" (
    echo BUILD FAILED - PyInstaller exited with %RC%. See the output above.
    echo dist\%APP% was not touched; it is still the previous build.
    pause
    exit /b 1
)
if not exist "%STAGE%\%APP%\%APP%.exe" (
    echo BUILD FAILED - PyInstaller reported success but produced no exe.
    echo dist\%APP% was not touched; it is still the previous build.
    pause
    exit /b 1
)

:: /XD takes the full path of the live data folder, so only that one folder is
:: spared - a package's own "data" directory inside _internal is still copied.
:: robocopy's exit codes below 8 all mean success.
echo Copying the build into dist\%APP% (data\ is left alone) ...
robocopy "%STAGE%\%APP%" "%LIVE%" /MIR /XD "%LIVE%\data" /R:2 /W:2 /NFL /NDL /NJH /NJS /NP
if errorlevel 8 (
    echo BUILD FAILED - the copy into dist\%APP% did not finish. The new build is
    echo complete in %STAGE%\%APP%; data\ was not touched.
    pause
    exit /b 1
)

echo SUCCESS: dist\%APP%\%APP%.exe
echo          ...\%APP%.exe --tracker  starts the tray tracker
pause
