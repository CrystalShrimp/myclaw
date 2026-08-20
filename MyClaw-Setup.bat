@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1
cd /d "%~dp0"

echo ===================================================
echo           MyClaw 环境依赖自检与自动安装工具
echo ===================================================
echo.

set NEED_RESTART_CMD=0

REM ================= 1. Node.js 20+ 版本检查 =================
set NODE_OK=0
where node >nul 2>&1
if errorlevel 1 goto CHECK_NODE_DONE

for /f "tokens=1 delims=v. " %%a in ('node -v') do set NODE_MAJOR=%%a
if not defined NODE_MAJOR goto CHECK_NODE_DONE
if %NODE_MAJOR% geq 20 set NODE_OK=1

:CHECK_NODE_DONE
if "%NODE_OK%"=="1" (
    echo [OK] Node.js 20+ 环境检查通过！
    goto CHECK_VENV
)

echo [!] 警告: 未检测到 Node.js 20+ 环境 (auto_feishu 需要 Node.js v20 或更高版本)。
set /p CHOICE_NODE="[?] 是否自动下载并静默安装 Node.js v20.18.0 LTS？ [Y/N]: "
if /i not "%CHOICE_NODE%"=="Y" if /i not "%CHOICE_NODE%"=="" (
    echo [-] 已跳过 Node.js 安装。
    goto CHECK_VENV
)

echo [!] 正在通过国内镜像下载 Node.js 20.18.0 官方安装包...
set "MSI_PATH=%TEMP%\node_v20.msi"
curl.exe -L -o "%MSI_PATH%" "https://npmmirror.com/mirrors/node/v20.18.0/node-v20.18.0-x64.msi"
if exist "%MSI_PATH%" (
    echo [!] 正在静默安装 Node.js...
    msiexec.exe /i "%MSI_PATH%" /quiet /norestart
    del /f /q "%MSI_PATH%" >nul 2>&1
    set NEED_RESTART_CMD=1
    set "PATH=%ProgramFiles%\nodejs;%PATH%"
    echo [OK] Node.js 20.18.0 已静默安装！
)

:CHECK_VENV
echo.
REM ================= 2. Python .venv 独立环境 =================
if exist ".venv\Scripts\python.exe" (
    echo [OK] Python 独立环境检查通过: .venv\Scripts\python.exe
    goto CHECK_FEISHU
)

echo [!] 警告: 未检测到 Python 独立环境 (.venv)。
set /p CHOICE_VENV="[?] 是否自动安装 uv 并创建 Python .venv 环境？ [Y/N]: "
if /i not "%CHOICE_VENV%"=="Y" if /i not "%CHOICE_VENV%"=="" (
    echo [-] 已跳过 Python .venv 创建。
    goto CHECK_FEISHU
)

where uv >nul 2>&1
if errorlevel 1 (
    echo [!] 正在自动使用 curl 下载并安装 uv 工具...
    set "UV_INSTALLER=%TEMP%\uv_install.ps1"
    curl.exe -L -o "%UV_INSTALLER%" "https://astral.sh/uv/install.ps1"
    if exist "!UV_INSTALLER!" (
        powershell -NoProfile -ExecutionPolicy Unrestricted -File "!UV_INSTALLER!"
        del /f /q "!UV_INSTALLER!" >nul 2>&1
    )
    set "PATH=%USERPROFILE%\.cargo\bin;%USERPROFILE%\.local\bin;%PATH%"
)

echo [!] 正在调用 uv sync 创建项目环境...
call uv sync
if exist ".venv\Scripts\python.exe" (
    echo [OK] Python 独立环境 (.venv) 创建成功！
) else (
    echo [X] .venv 创建失败，请检查 Python 和 uv 的网络连接。
)

:CHECK_FEISHU
echo.
REM ================= 3. auto_feishu npm 和 Playwright 依赖 =================
if not exist "auto_feishu\package.json" goto CHECK_ENV
if exist "auto_feishu\node_modules" (
    echo [OK] auto_feishu node_modules 依赖检查通过！
    goto CHECK_ENV
)

echo [!] 警告: 未检测到 auto_feishu 的 Node.js 依赖包 (node_modules)。
set /p CHOICE_NPM="[?] 是否自动安装 auto_feishu 依赖和 Playwright 浏览器？ [Y/N]: "
if /i not "%CHOICE_NPM%"=="Y" if /i not "%CHOICE_NPM%"=="" (
    echo [-] 已跳过 auto_feishu npm 依赖安装。
    goto CHECK_ENV
)

pushd auto_feishu
echo [!] 正在执行 npm install ...
call npm install
echo [!] 正在安装 Playwright Chromium 浏览器内核...
call npx playwright install chromium
popd
echo [OK] auto_feishu 依赖和 Playwright 浏览器安装完成！

:CHECK_ENV
echo.
REM ================= 4. .env 环境文件检查 =================
if exist ".env" (
    echo [OK] 环境文件 .env 已通过检查！
    goto FINISH
)

if not exist "examples\.env.example" goto FINISH
echo [!] 警告: 未检测到环境文件 .env。
set /p CHOICE_ENV="[?] 是否自动从 examples\.env.example 创建初始 .env 环境文件？ [Y/N]: "
if /i not "%CHOICE_ENV%"=="Y" if /i not "%CHOICE_ENV%"=="" goto FINISH
copy "examples\.env.example" ".env" >nul
echo [OK] 初始 .env 环境文件已创建，请用文本编辑器填入飞书应用凭据。

:FINISH
echo.
echo ===================================================
echo              自检和依赖安装已完成！
if "%NEED_RESTART_CMD%"=="1" (
    echo [注意] 已安装全局系统组件，请重新打开 cmd 窗口让环境变量完全生效。
)
echo ===================================================
pause
