@echo off
:: Start the background playlist tracker (tray icon only, no window).
::
:: The built exe comes first here, unlike run.cmd: autostart points at the exe,
:: and each copy keeps its own data folder, so launching the source copy by hand
:: would quietly count into a second, different tracker.json.
cd /d "%~dp0"
if exist "dist\WallpaperEngineToolkit\WallpaperEngineToolkit.exe" (
    start "" "dist\WallpaperEngineToolkit\WallpaperEngineToolkit.exe" --tracker
) else if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" run_app.py --tracker
) else (
    start "" pythonw run_app.py --tracker
)
