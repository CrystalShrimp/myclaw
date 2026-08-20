@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

if /I "%HTTP_PROXY%"=="http://127.0.0.1:6984" set HTTP_PROXY=
if /I "%HTTPS_PROXY%"=="http://127.0.0.1:6984" set HTTPS_PROXY=
if /I "%http_proxy%"=="http://127.0.0.1:6984" set http_proxy=
if /I "%https_proxy%"=="http://127.0.0.1:6984" set https_proxy=

echo ===================================================
echo             auto_feishu 环境自检与一键设置
echo ===================================================
echo.

REM === 1. Node.js 20+ 版本检测 ===
set NODE_OK=0
where node >nul 2>&1
if errorlevel 1 goto CHECK_NODE_DONE

for /f "tokens=1 delims=v. " %%a in ('node -v') do set NODE_MAJOR=%%a
if not defined NODE_MAJOR goto CHECK_NODE_DONE
if %NODE_MAJOR% geq 20 set NODE_OK=1

:CHECK_NODE_DONE
if "%NODE_OK%"=="1" (
    echo [OK] Node.js 20+ 检测通过。
    goto DO_NPM_INSTALL
)

echo [!] 警告: 未检测到 Node.js 20+ 环境 (auto_feishu 需要 Node.js >= 20.0.0)。
set /p CHOICE_NODE="[?] 是否自动下载并静默安装 Node.js v20.18.0 LTS？ [Y/N]: "
if /i not "%CHOICE_NODE%"=="Y" (
    echo [ERROR] 缺少 Node.js 20+，无法运行 auto_feishu 飞书自动化建置。
    pause
    exit /b 1
)

echo [!] 正在通过原生 curl 下载 Node.js 20.18.0 官方安装包...
set "MSI_PATH=%TEMP%\node_v20.msi"
curl.exe -L -o "%MSI_PATH%" "https://npmmirror.com/mirrors/node/v20.18.0/node-v20.18.0-x64.msi"
if exist "%MSI_PATH%" (
    echo [!] 正在静默安装 Node.js...
    msiexec.exe /i "%MSI_PATH%" /quiet /norestart
    del /f /q "%MSI_PATH%" >nul 2>&1
    set "PATH=%ProgramFiles%\nodejs;%PATH%"
    echo [OK] Node.js 20.18.0 安装指令完成！
)

:DO_NPM_INSTALL
echo.
REM === 2. 锁定的 npm 依赖安装 ===
if not exist node_modules\.bin\tsx.cmd (
    echo [INFO] Installing locked Node dependencies...
    call npm ci --ignore-scripts
    if errorlevel 1 (
        echo [ERROR] npm ci failed.
        pause
        exit /b 1
    )
)

REM === 3. Chromium pre-check ===
echo [INFO] Checking Playwright Chromium...
call npx playwright install --dry-run chromium >nul 2>&1
if errorlevel 1 (
    echo [INFO] Chromium not installed, downloading...
    call npx playwright install chromium
    if errorlevel 1 (
        echo [ERROR] Chromium install failed.
        pause
        exit /b 1
    )
) else (
    echo [INFO] Chromium already installed, skip download.
)

echo.
echo [INFO] Starting Feishu one-click setup...
call npm run feishu:setup
if errorlevel 1 (
    echo [ERROR] Feishu setup failed.
    pause
    exit /b 1
)
echo.
echo [OK] Feishu setup completed.
pause
exit /b 0
