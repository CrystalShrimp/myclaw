#!/bin/bash
# MyClaw macOS 一键安装/自检向导（等价 Windows 的 MyClaw-Setup.bat）。
set -u
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

echo "======================================"
echo "      MyClaw macOS 安装向导"
echo "======================================"

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
echo "[OK] Python 虚拟环境依赖安装完成！"

# 3. .env 检查
if [ ! -f ".env" ]; then
    if [ -f "examples/.env.example" ]; then
        cp "examples/.env.example" ".env"
        echo "[OK] 已从 examples/.env.example 生成 .env 模板。"
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

# 5. 模型与供应商配置
echo ""
echo "======================================"
echo "    模型与供应商初始化配置"
echo "======================================"
if [ -x ".venv/bin/python" ]; then
    ".venv/bin/python" scripts/manage_models.py
else
    python3 scripts/manage_models.py
fi

echo ""
echo "======================================"
echo "[SUCCESS] MyClaw 安装与初始化完成！"
echo "  启动服务      : 双击 MyClaw.command（或运行 bash scripts/restart_mac.sh）"
echo "  配置飞书/企微 : 在终端执行 ./setup.sh"
echo "  管理/切换模型 : 在终端执行 ./setup.sh 选 6"
echo "  开机自启      : bash scripts/setup_autostart_mac.sh"
echo "  健康检查      : curl http://127.0.0.1:8080/health"
echo "======================================"
