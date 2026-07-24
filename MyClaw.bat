@echo off
cd /d "%~dp0"
if not exist .env (
    echo [ERROR] .env file not found!
    pause
    exit /b 2
)
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0scripts\tray.pyw"