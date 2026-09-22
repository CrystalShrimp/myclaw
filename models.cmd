@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" scripts\manage_models.py
) else (
    python scripts\manage_models.py
)

if errorlevel 1 (
    echo.
    pause
)
