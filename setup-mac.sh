#!/bin/bash
# MyClaw macOS 一键安装/自检向导（等价 Windows 的 MyClaw-Setup.bat）。
set -u
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

echo "======================================"
echo "     MyClaw macOS 安装向导"
echo "======================================"

# 1. uv 检查
if ! command -v uv >/dev/null 2>&1; then
    echo "[!] 未检测到 uv，正在安装（官方脚本）..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
    command -v uv >/dev/null 2>&1 || { echo "[ERROR] uv 安装失败，请手动安装后重试。"; exit 1; }
fi
echo "[OK] uv $(uv --version | awk '{print $2}')"

# 2. Python 依赖
echo "[!] 正在执行 uv sync 安装依赖..."
uv sync || { echo "[ERROR] uv sync 失败。"; exit 1; }
echo "[OK] Python 依赖安装完成！"

# 3. .env 检查
if [ ! -f ".env" ]; then
    echo "[!] 未检测到 .env，正在从模板生成..."
    {
        echo "# ===== Feishu App Config ====="
        echo "FEISHU_APP_ID="
        echo "FEISHU_APP_SECRET="
        echo "FEISHU_VERIFICATION_TOKEN="
        echo "FEISHU_ENCRYPT_KEY="
        echo ""
        echo "# ===== Agent Config ====="
        echo "DEFAULT_WORKSPACE="
        echo ""
        echo "# ===== Access Control ====="
        echo "ALLOWED_USERS="
        echo ""
        echo "# ===== Server Config ====="
        echo "HOST=0.0.0.0"
        echo "PORT=8080"
        echo "INSTANCE_LOCK_PORT=48921"
    } > ".env"
    echo "[OK] 已生成 .env 模板，请稍后编辑填入飞书凭据 (FEISHU_APP_ID / FEISHU_APP_SECRET)。"
fi

# 4. Claude Code CLI 与供应商 profile
if ! command -v claude >/dev/null 2>&1; then
    echo "[!] 未检测到 Claude Code CLI，正在安装 (npm 全局)..."
    npm install -g @anthropic-ai/claude-code || { echo "[ERROR] 安装失败，请手动执行: npm install -g @anthropic-ai/claude-code"; exit 1; }
fi
echo "[OK] Claude CLI $(claude --version 2>/dev/null | head -1)"

if ! ls config/settings_*.json >/dev/null 2>&1; then
    echo "[!] 警告: 未发现供应商 profile (config/settings_<name>.json)。"
    echo "    myclaw 需要至少一个 profile 提供 ANTHROPIC_BASE_URL / API Key，"
    echo "    参考 examples/ 模板创建后在飞书里 /provider 选择。"
else
    echo "[OK] 供应商 profile: $(ls config/settings_*.json | xargs -n1 basename | tr '\n' ' ')"
fi

# 5. 启动方式
echo ""
echo "======================================"
echo "[SUCCESS] 安装完成！"
echo "  启动服务      : 双击 MyClaw.command（或 bash scripts/restart_mac.sh）"
echo "  菜单栏方式(可选): .venv/bin/pip install rumps && .venv/bin/python scripts/menubar.py"
echo "  开机自启      : bash scripts/setup_autostart_mac.sh"
echo "  健康检查      : curl http://127.0.0.1:8080/health"
echo "======================================"
