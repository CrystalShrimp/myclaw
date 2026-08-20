@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1
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
if %errorlevel% equ 0 (
    for /f "tokens=1,2,3 delims=v." %%a in ('node -v 2^>nul') do (
        set NODE_MAJOR=%%a
        if !NODE_MAJOR! geq 20 (
            set NODE_OK=1
            echo [✓] Node.js 检测通过: v!NODE_MAJOR! (>= 20)
        )
    )
)

if !NODE_OK! equ 0 (
    echo [!] 警告: 未检测到 Node.js 20+ 环境 (auto_feishu 需要 Node.js >= 20.0.0)。
    set /p CHOICE_NODE="[?] 是否自动下载并静默安装 Node.js v20.18.0 LTS？ [Y/N]: "
    if /i "!CHOICE_NODE!"=="Y" (
        where winget >nul 2>&1
        if !errorlevel! equ 0 (
            echo [!] 正在通过 Windows 包管理器 winget 安装 Node.js 20...
            winget install OpenJS.NodeJS.LTS --accept-package-agreements --accept-source-agreements
        ) else (
            echo [!] 正在通过原生 curl 下载 Node.js 20.18.0 官方安装包...
            set "MSI_PATH=%TEMP%\node_v20.msi"
            curl.exe -L -o "!MSI_PATH!" "https://npmmirror.com/mirrors/node/v20.18.0/node-v20.18.0-x64.msi"
            if exist "!MSI_PATH!" (
                echo [!] 正在静默安装 Node.js...
                msiexec.exe /i "!MSI_PATH!" /quiet /norestart
                del /f /q "!MSI_PATH!" >nul 2>&1
            )
        )
        set PATH=%ProgramFiles%\nodejs;%PATH%
        echo [✓] Node.js 20.18.0 安装指令完成！
    ) else (
        echo [ERROR] 缺少 Node.js 20+，无法运行 auto_feishu 飞书自动化建置。
        pause
        exit /b 1
    )
)
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

REM === 3. Chromium pre-check + friendly error ===
echo [INFO] Checking Playwright Chromium...
call npx playwright install --dry-run chromium >nul 2>&1
if errorlevel 1 (
    echo [INFO] Chromium not installed, downloading...
    call npx playwright install chromium
    if errorlevel 1 (
        echo [ERROR] Chromium install failed. Without it Feishu automation cannot run.
        echo   Common causes:
        echo     1. Network / firewall / GFW blocked the download
        echo     2. Company proxy required (set HTTP_PROXY^)
        echo     3. Disk space issue
        echo   Retry manually:  cd auto_feishu ^&^& npx playwright install chromium
        exit /b 1
    )
) else (
    echo [INFO] Chromium already installed, skip download.
)

echo.
echo [INFO] Starting Feishu one-click setup...
call npm run feishu:setup
if errorlevel 1 (
    echo.
    echo [ERROR] Feishu setup failed. Messages and artifacts above.
    echo Re-run setup.cmd to resume from the last completed step.
    pause
    exit /b 1
)
echo.
echo [OK] Feishu setup completed.
pause
exit /b 0
