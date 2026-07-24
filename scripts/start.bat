@echo off
cd /d "%~dp0.."
if not exist .env exit /b 2
if not exist .venv\Scripts\pythonw.exe exit /b 3
start "" ".venv\Scripts\pythonw.exe" "scripts\tray.pyw"