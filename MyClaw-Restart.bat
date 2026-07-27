@echo off
chcp 65001 >nul 2>&1
title MyClaw Restart
cd /d "%~dp0"

if not exist .venv\Scripts\python.exe (
    echo [ERROR] .venv not found. Run: uv sync
    pause
    exit /b 3
)

echo Restarting MyClaw service...
echo.
".venv\Scripts\python.exe" "scripts\restart_service.py"
set "EXITCODE=%ERRORLEVEL%"
echo.
echo restart_service.py exited with code %EXITCODE%
pause
exit /b %EXITCODE%
