@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1
cd /d "%~dp0"

echo ===================================================
echo           MyClaw 环境先决条件自检与自动安装程序
echo ===================================================
echo.

set NEED_RESTART_CMD=0

REM ================= 1. Node.js 20+ 版本检测 =================
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
        set NEED_RESTART_CMD=1
        set PATH=%ProgramFiles%\nodejs;%PATH%
        echo [✓] Node.js 20.18.0 静默安装完成！
    ) else (
        echo [-] 已跳过 Node.js 安装。
    )
)
echo.

REM ================= 2. Python .venv 虚拟环境检测 =================
set VENV_OK=0
if exist ".venv\Scripts\python.exe" set VENV_OK=1

if !VENV_OK! equ 0 (
    echo [!] 警告: 未检测到 Python 虚拟环境 (.venv)。
    set /p CHOICE_VENV="[?] 是否自动安装 uv 并构建 Python .venv 依赖？ [Y/N]: "
    if /i "!CHOICE_VENV!"=="Y" (
        where uv >nul 2>&1
        if !errorlevel! neq 0 (
            echo [!] 正在自动使用 curl 下载并安装 uv 工具...
            set "UV_INSTALLER=%TEMP%\uv_install.ps1"
            curl.exe -L -o "!UV_INSTALLER!" "https://astral.sh/uv/install.ps1"
            if exist "!UV_INSTALLER!" (
                powershell -NoProfile -ExecutionPolicy Unrestricted -File "!UV_INSTALLER!"
                del /f /q "!UV_INSTALLER!" >nul 2>&1
            )
            set PATH=%USERPROFILE%\.cargo\bin;%USERPROFILE%\.local\bin;%PATH%
        )
        echo [!] 正在调用 uv sync 构建项目虚拟环境...
        uv sync
        if exist ".venv\Scripts\python.exe" (
            echo [✓] Python 虚拟环境 (.venv) 构建完成！
        ) else (
            echo [X] .venv 构建失败，请检查 Python 或 uv 网络连接。
        )
    ) else (
        echo [-] 已跳过 Python .venv 构建。
    )
) else (
    echo [✓] Python 虚拟环境检测通过: .venv\Scripts\python.exe
)
echo.

REM ================= 3. auto_feishu npm 与 Playwright 检测 =================
if exist "auto_feishu\package.json" (
    if not exist "auto_feishu\node_modules" (
        echo [!] 警告: 未检测到 auto_feishu 的 Node.js 依赖包 (node_modules)。
        set /p CHOICE_NPM="[?] 是否自动安装 auto_feishu 依赖及 Playwright 浏览器？ [Y/N]: "
        if /i "!CHOICE_NPM!"=="Y" (
            pushd auto_feishu
            echo [!] 正在运行 npm install ...
            call npm install
            echo [!] 正在安装 Playwright Chromium 浏览器组件...
            call npx playwright install chromium
            popd
            echo [✓] auto_feishu 依赖与 Playwright 浏览器安装完成！
        ) else (
            echo [-] 已跳过 auto_feishu npm 依赖安装。
        )
    ) else (
        echo [✓] auto_feishu node_modules 检测通过。
    )
)
echo.

REM ================= 4. .env 配置文件检测 =================
if not exist ".env" (
    if exist "examples\.env.example" (
        echo [!] 警告: 未检测到配置文件 .env。
        set /p CHOICE_ENV="[?] 是否自动从 examples\.env.example 创建初始 .env 配置文件？ [Y/N]: "
        if /i "!CHOICE_ENV!"=="Y" (
            copy "examples\.env.example" ".env" >nul
            echo [✓] 初始 .env 配置文件已创建，请使用文本编辑器填写您的飞书凭证。
        )
    )
) else (
    echo [✓] 配置文件 .env 检测通过。
)
echo.

echo ===================================================
echo              环境自检与引导设置完成！
if !NEED_RESTART_CMD! equ 1 (
    echo [注意] 已安装全新系统组件，建议重新打开 cmd 窗口使环境变量全局生效。
)
echo ===================================================
pause
