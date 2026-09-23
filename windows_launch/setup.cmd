@echo off
setlocal
cd /d "%~dp0.."

echo ==============================================
echo            MyClaw 平台配置
echo ==============================================
echo.
echo  1. 配置飞书 - 个人用（仅创建者可用，不改变应用可用范围）
echo  2. 配置飞书 - 公用（可用范围全员，支持群成员一键导入白名单）
echo  3. 飞书群成员一键导入白名单（日常维护工具）
echo  4. 配置企业微信（手动填 + 连通实测）
echo  5. 完整配置（飞书公用 + 企业微信）
echo  6. 模型与供应商管理（查看/切换/新增/编辑/测试/删除）
echo  0. 退出
echo.
set /p choice=请选择 [0/1/2/3/4/5/6]: 

if "%choice%"=="1" goto feishu_personal
if "%choice%"=="2" goto feishu_public
if "%choice%"=="3" goto import_group
if "%choice%"=="4" goto wecom
if "%choice%"=="5" goto full
if "%choice%"=="6" goto models
goto end

:feishu_personal
set "FEISHU_DEPLOY_MODE=personal"
call auto_feishu\setup.cmd
set "FEISHU_DEPLOY_MODE="
goto end

:feishu_public
call auto_feishu\setup.cmd
call :ask_group_import
goto end

:import_group
set "RUN_TARGET=scripts\import_feishu_group.py"
goto pyrun

:wecom
set "RUN_TARGET=scripts\setup_wecom.py"
goto pyrun

:full
call auto_feishu\setup.cmd
call :ask_group_import
echo.
echo 接下来进行企业微信配置...
set "RUN_TARGET=scripts\setup_wecom.py"
goto pyrun

:models
set "RUN_TARGET=scripts\manage_models.py"
goto pyrun

:pyrun
if not exist .\venv\Scripts\python.exe (
    echo [X] 未找到 .venv，请先运行本目录 MyClaw-Setup.bat 完成环境安装
    goto end
)
".venv\Scripts\python.exe" %RUN_TARGET%
if errorlevel 1 pause
goto end

:ask_group_import
echo.
echo ==============================================
echo          飞书公用模式 - 白名单配置
echo ==============================================
echo 默认模式：ALLOWED_USERS 保持为空，企业内全员均可直接访问。
echo.
set "IMPORT_CHOICE="
set /p IMPORT_CHOICE=[?] 是否将特定群聊的所有用户 ID 一键导入为白名单？[Y/N] (默认 N): 
if /i "%IMPORT_CHOICE%"=="Y" (
    ".venv\Scripts\python.exe" scripts\import_feishu_group.py
) else (
    ".venv\Scripts\python.exe" -c "from scripts.import_feishu_group import read_env, upsert_env; wecom=[u for u in read_env().get('ALLOWED_USERS','').split(',') if u.strip().startswith('wecom:')]; upsert_env('ALLOWED_USERS', ','.join(wecom))"
    echo [OK] 已将 ALLOWED_USERS 设为全员开放模式。
)
exit /b 0

:end
echo.
pause
