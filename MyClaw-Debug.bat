@echo off
chcp 65001 >nul 2>&1
title myclaw Debug Console
cd /d "%~dp0"

if not exist .env (
    echo [ERROR] .env file not found.
    echo Copy .env.example to .env and complete the configuration first.
    pause
    exit /b 2
)

if not exist .venv\Scripts\python.exe (
    echo [ERROR] .venv not found. Run: uv sync
    pause
    exit /b 3
)

curl.exe --silent --fail --max-time 1 http://127.0.0.1:8080/health >nul 2>&1
if not errorlevel 1 (
    echo [ERROR] myclaw is already running on port 8080.
    echo Right-click the myclaw tray icon and select Exit myclaw, then run this file again.
    pause
    exit /b 4
)

echo Starting myclaw in debug console mode...
echo Press Ctrl+C to stop the service.
echo.

set HTTP_PROXY=
set HTTPS_PROXY=
set ALL_PROXY=
set http_proxy=
set https_proxy=
set all_proxy=

.venv\Scripts\python.exe -m app.main

echo.
echo myclaw stopped. Exit code: %ERRORLEVEL%
pause
