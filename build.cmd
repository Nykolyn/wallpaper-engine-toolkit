@echo off
:: Build a standalone WallpaperEngineToolkit.exe with PyInstaller.
cd /d "%~dp0"

:: PyInstaller wipes dist\WallpaperEngineToolkit before writing, and that is exactly
:: where a deployed copy keeps its live state: the Rotator's config.json and
:: history.json, suite.json, tracker.json. Stash the folder and put it back.
set "KEEP=%TEMP%\WallpaperEngineToolkit_data_backup"
if exist "dist\WallpaperEngineToolkit\data" (
    echo Preserving dist\WallpaperEngineToolkit\data ...
    if exist "%KEEP%" rmdir /s /q "%KEEP%"
    xcopy "dist\WallpaperEngineToolkit\data" "%KEEP%\" /e /i /q /y >nul
)

echo Building WallpaperEngineToolkit.exe ...
if exist ".venv\Scripts\pyinstaller.exe" (
    set "PYI=.venv\Scripts\pyinstaller.exe"
) else (
    set "PYI=pyinstaller"
)

:: This file must stay CRLF and plain ASCII. cmd.exe reads it in the OEM code
:: page, and a caret continuation followed by a bare LF does not continue - the
:: command below then runs with no program name and exits 9009.
::
:: --icon sets what Explorer and the taskbar show; --add-data ships the same
:: file so QIcon can paint the title bar and the Alt-Tab entry at runtime.
::
:: pymongo reaches the authors database and resolves mongodb+srv through
:: dnspython, both of which import lazily - PyInstaller cannot see either by
:: reading the source, so they are collected by name, or the built exe starts
:: fine and fails the moment the Review tab is opened.
::
:: Nothing may be commented out INSIDE the command below: a caret continues the
:: line, and "::" on a continued line is handed to PyInstaller as an argument
:: rather than skipped. That went unnoticed for four days - every build died on
:: "Script file '::' does not exist" while the check at the end still printed
:: SUCCESS, because it only asked whether an exe existed, not whether this run
:: had made one. Both halves of that are fixed: comments live up here, and the
:: exit code is what decides.
%PYI% ^
  --noconfirm ^
  --windowed ^
  --name "WallpaperEngineToolkit" ^
  --icon "assets\icon.ico" ^
  --add-data "assets\icon.ico;assets" ^
  --collect-submodules app ^
  --collect-all imageio_ffmpeg ^
  --collect-submodules pymongo ^
  --collect-submodules bson ^
  --collect-submodules dns ^
  run_app.py
set "RC=%ERRORLEVEL%"

if exist "%KEEP%" (
    echo Restoring dist\WallpaperEngineToolkit\data ...
    xcopy "%KEEP%" "dist\WallpaperEngineToolkit\data\" /e /i /q /y >nul
    rmdir /s /q "%KEEP%"
)

echo.
if not "%RC%"=="0" (
    echo BUILD FAILED - PyInstaller exited with %RC%. See the output above.
    echo Whatever is in dist\ is the PREVIOUS build; this run did not replace it.
) else if exist "dist\WallpaperEngineToolkit\WallpaperEngineToolkit.exe" (
    echo SUCCESS: dist\WallpaperEngineToolkit\WallpaperEngineToolkit.exe
    echo          ...\WallpaperEngineToolkit.exe --tracker  starts the tray tracker
) else (
    echo BUILD FAILED - PyInstaller reported success but produced no exe.
)
pause
