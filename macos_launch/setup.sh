#!/bin/bash
# MyClaw 平台配置向导 (macOS / Linux)
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

PY="python3"
if [ -x ".venv/bin/python" ]; then
    PY=".venv/bin/python"
fi

while true; do
    echo "=============================================="
    echo "           MyClaw 平台配置向导"
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
            break
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
            break
            ;;
        3)
            "$PY" scripts/import_feishu_group.py
            break
            ;;
        4)
            "$PY" scripts/setup_wecom.py
            break
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
            break
            ;;
        6)
            "$PY" scripts/manage_models.py
            break
            ;;
        0|q|exit)
            echo "退出向导。"
            exit 0
            ;;
        *)
            echo "[!] 无效选项，请重新输入。"
            ;;
    esac
done
