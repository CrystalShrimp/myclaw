#!/bin/bash
# MyClaw macOS 安装与配置向导（等价 Windows 的 MyClaw-Setup.bat）。
# 首次运行：安装 uv/Python 依赖、生成 .env、检查 Node/Claude CLI；
# 随后进入配置菜单（飞书/企微/白名单/模型），可随时重跑只做配置。
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

PY="python3"
if [ -x ".venv/bin/python" ]; then
    PY=".venv/bin/python"
fi

echo "=============================================="
echo "        MyClaw macOS 安装与配置向导"
echo "=============================================="

# ---- 安装部分（已装则自动跳过） ----

# 1. uv 检查
if ! command -v uv >/dev/null 2>&1; then
    echo "[!] 未检测到 uv，正在安装（官方脚本）..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
    command -v uv >/dev/null 2>&1 || { echo "[ERROR] uv 安装失败，请手动安装后重试。"; exit 1; }
fi
echo "[OK] uv $(uv --version 2>/dev/null | awk '{print $2}')"

# 2. Python 依赖
echo "[!] 正在执行 uv sync 安装 Python 依赖..."
uv sync || { echo "[ERROR] uv sync 失败。"; exit 1; }
PY=".venv/bin/python"
echo "[OK] Python 虚拟环境依赖安装完成！"

# 3. .env 检查
if [ ! -f ".env" ]; then
    if [ -f "config/env.example" ]; then
        cp "config/env.example" ".env"
        echo "[OK] 已从 config/env.example 生成 .env 模板。"
    else
        cat > ".env" <<EOF
# ===== Feishu App Config =====
FEISHU_APP_ID=
FEISHU_APP_SECRET=
FEISHU_VERIFICATION_TOKEN=
FEISHU_ENCRYPT_KEY=

# ===== WeCom Bot Config =====
WECOM_BOT_ID=
WECOM_SECRET=

# ===== Access Control =====
ALLOWED_USERS=

# ===== Server Config =====
HOST=0.0.0.0
PORT=8080
EOF
        echo "[OK] 已生成基础 .env 模板。"
    fi
fi

# 4. Node.js 与 Claude Code CLI 检查
if ! command -v node >/dev/null 2>&1; then
    echo "[WARN] 未检测到 Node.js，请安装 Node.js 20+ (可使用 brew install node)。"
fi

if ! command -v claude >/dev/null 2>&1; then
    echo "[!] 未检测到 Claude CLI，建议在终端运行安装："
    echo "    npm install -g @anthropic-ai/claude-code"
else
    echo "[OK] Claude Code CLI $(claude --version 2>/dev/null | head -1)"
fi

# ---- 配置菜单 ----

while true; do
    echo ""
    echo "=============================================="
    echo "           MyClaw 配置菜单"
    echo "=============================================="
    echo ""
    echo " 1. 配置飞书 - 个人用（仅创建者可用，不改变应用可用范围）"
    echo " 2. 配置飞书 - 公用（可用范围全员，支持群成员一键导入白名单）"
    echo " 3. 飞书群成员一键导入白名单（日常维护工具）"
    echo " 4. 配置企业微信（手动填 + 连通实测）"
    echo " 5. 完整配置（飞书公用 + 企业微信）"
    echo " 6. 模型与供应商管理（切换/添加/修改/测试模型）"
    echo " 0. 退出"
    echo ""
    printf "请选择 [0/1/2/3/4/5/6]: "
    read -r choice

    case "$choice" in
        1)
            if [ -f "auto_feishu/setup.sh" ]; then
                bash auto_feishu/setup.sh personal
            else
                echo "[!] auto_feishu/setup.sh 正在准备中..."
            fi
            echo ""
            echo "[OK] 飞书个人用模式配置完成。"
            ;;
        2)
            if [ -f "auto_feishu/setup.sh" ]; then
                bash auto_feishu/setup.sh public
            fi
            echo ""
            echo "=============================================="
            echo "          飞书公用模式 - 白名单配置"
            echo "=============================================="
            echo "💡 默认模式：ALLOWED_USERS 保持为空，企业内全员均可直接访问。"
            echo ""
            printf "[?] 是否需要将特定群聊的所有用户ID一键导入为白名单？[Y/N] (默认 N): "
            read -r IMPORT_CHOICE
            if [[ "$IMPORT_CHOICE" =~ ^[Yy]$ ]]; then
                "$PY" scripts/import_feishu_group.py
            else
                "$PY" -c "from scripts.import_feishu_group import read_env, upsert_env; env=read_env(); wecom=[u for u in env.get('ALLOWED_USERS','').split(',') if u.strip().startswith('wecom:')]; upsert_env('ALLOWED_USERS', ','.join(wecom))"
                echo "[OK] 已将 ALLOWED_USERS 设为全员开放模式。"
            fi
            ;;
        3)
            "$PY" scripts/import_feishu_group.py
            ;;
        4)
            "$PY" scripts/setup_wecom.py
            ;;
        5)
            if [ -f "auto_feishu/setup.sh" ]; then
                bash auto_feishu/setup.sh public
            fi
            echo ""
            echo "=============================================="
            echo "          飞书公用模式 - 白名单配置"
            echo "=============================================="
            echo "💡 默认模式：ALLOWED_USERS 保持为空，企业内全员均可直接访问。"
            echo ""
            printf "[?] 是否需要将特定群聊的所有用户ID一键导入为白名单？[Y/N] (默认 N): "
            read -r IMPORT_CHOICE
            if [[ "$IMPORT_CHOICE" =~ ^[Yy]$ ]]; then
                "$PY" scripts/import_feishu_group.py
            else
                "$PY" -c "from scripts.import_feishu_group import read_env, upsert_env; env=read_env(); wecom=[u for u in env.get('ALLOWED_USERS','').split(',') if u.strip().startswith('wecom:')]; upsert_env('ALLOWED_USERS', ','.join(wecom))"
                echo "[OK] 已将 ALLOWED_USERS 设为全员开放模式。"
            fi
            echo ""
            echo "接下来进行企业微信配置..."
            "$PY" scripts/setup_wecom.py
            ;;
        6)
            "$PY" scripts/manage_models.py
            ;;
        0|q|exit)
            echo ""
            echo "=============================================="
            echo "[SUCCESS] MyClaw 安装与配置完成！"
            echo "  启动服务      : 双击本目录 MyClaw.command（或运行 bash scripts/restart_mac.sh）"
            echo "  重新配置      : 重跑本脚本 ./setup.sh"
            echo "  开机自启      : bash scripts/setup_autostart_mac.sh"
            echo "  健康检查      : curl http://127.0.0.1:8080/health"
            echo "=============================================="
            exit 0
            ;;
        *)
            echo "[!] 无效选项，请重新输入。"
            ;;
    esac
done
