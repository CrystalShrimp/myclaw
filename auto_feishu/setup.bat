@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0"

if /I "%HTTP_PROXY%"=="http://127.0.0.1:6984" set HTTP_PROXY=
if /I "%HTTPS_PROXY%"=="http://127.0.0.1:6984" set HTTPS_PROXY=
if /I "%http_proxy%"=="http://127.0.0.1:6984" set http_proxy=
if /I "%https_proxy%"=="http://127.0.0.1:6984" set https_proxy=

where node >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Node.js 20 or newer is required.
  exit /b 1
)

if not exist node_modules\.bin\tsx.cmd (
  echo [INFO] Installing locked Node dependencies...
  call npm ci --ignore-scripts
  if errorlevel 1 exit /b 1
)

echo [INFO] Preparing Playwright Chromium...
call npx playwright install chromium
if errorlevel 1 exit /b 1

echo [INFO] Starting Feishu one-click setup...
call npm run feishu:setup
exit /b %errorlevel%
