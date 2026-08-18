@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"

if not exist .env (
    echo [ERROR] .env file not found!
    echo Copy examples\.env.example to .env and fill in Feishu credentials first.
    pause
    exit /b 2
)

if not exist ".venv\Scripts\pythonw.exe" (
    echo [ERROR] .venv not found: "%~dp0.venv\Scripts\pythonw.exe"
    echo Run this once to install dependencies:
    echo     uv sync
    pause
    exit /b 3
)

REM === Cleanup stale myclaw processes (any Python flavor) before launch ===
REM Avoids "two trays fighting for mutex → no tray icon" issue when Anaconda
REM or leftover processes from previous runs are still alive.
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'python' -and ($_.CommandLine -match 'tray\.pyw' -or $_.CommandLine -match 'app\.main') } | ForEach-Object { Write-Host ('Killing stale PID ' + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
REM Give the OS a moment to release the mutex and TCP port
timeout /t 1 /nobreak >nul 2>&1

start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0scripts\tray.pyw"
