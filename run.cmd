@echo off
:: Launch the Wallpaper Engine Toolkit (Copier + Creator + Rotator in one window).
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" run_app.py
) else (
    python run_app.py
)
