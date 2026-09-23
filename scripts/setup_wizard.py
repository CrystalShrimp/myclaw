#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MyClaw 跨平台交互配置向导。
负责：环境自检（Node.js / Claude CLI / .env / 模型供应商）与全功能配置中心（飞书/企微/白名单/模型管理）。
"""

import os
import sys
import shutil
import subprocess
from pathlib import Path

# 确保 Windows 下控制台 Unicode 安全输出
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleCP(65001)
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
    except Exception:
        pass
    try:
        if sys.stdout and hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if sys.stderr and hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        if sys.stdin and hasattr(sys.stdin, "reconfigure"):
            sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT_DIR = Path(__file__).resolve().parent.parent


def run_cmd(cmd: list[str], cwd: Path | None = None, check: bool = False, env: dict | None = None) -> int:
    """运行子命令并实时透传标准输入输出。"""
    c_env = os.environ.copy()
    if env:
        c_env.update(env)
    # 在 Windows 下如果命令是 npm 或 npx，需开启 shell=True 或解析为 npm.cmd
    use_shell = sys.platform == "win32" and cmd[0] in ("npm", "npx")
    res = subprocess.run(cmd, cwd=cwd or ROOT_DIR, env=c_env, shell=use_shell)
    if check and res.returncode != 0:
        sys.exit(res.returncode)
    return res.returncode


def check_prerequisites():
    """预检基础环境（Node.js、.env、Claude CLI、模型配置）。"""
    print("=" * 50)
    print("           MyClaw 环境自检与依赖检查")
    print("=" * 50)

    # 1. 检查 Node.js
    node_path = shutil.which("node")
    if not node_path:
        print("[!] 警告: 未检测到 Node.js 环境（auto_feishu 需要 Node.js v20+）。")
        print("    建议前往 https://nodejs.org 下载安装 Node.js LTS 版本。")
    else:
        try:
            ver = subprocess.check_output(["node", "-v"], text=True).strip()
            print(f"[OK] Node.js 环境: {ver}")
        except Exception:
            print("[OK] 检测到 Node.js")

    # 2. 检查 .env
    env_file = ROOT_DIR / ".env"
    example_file = ROOT_DIR / "config" / "env.example"
    if not env_file.exists():
        if example_file.exists():
            shutil.copy(example_file, env_file)
            print("[OK] 已从 config/env.example 初始化生成 .env 文件。")
        else:
            print("[!] 未找到 config/env.example，建议手动创建 .env 文件。")
    else:
        print("[OK] .env 配置文件已就绪。")

    # 3. 检查 Claude Code CLI
    claude_path = shutil.which("claude")
    if not claude_path:
        print("[!] 提示: 未检测到 Claude Code CLI。")
        choice = input("[?] 是否立即通过 npm 全局安装 Claude CLI？[Y/N] (默认 N): ").strip().lower()
        if choice == "y":
            print("[*] 正在安装 @anthropic-ai/claude-code ...")
            run_cmd(["npm", "install", "-g", "@anthropic-ai/claude-code", "--registry=https://registry.npmmirror.com"])
    else:
        print("[OK] Claude Code CLI 已就绪。")

    # 4. 检查模型配置
    active_profile_file = ROOT_DIR / "config" / "active_profile"
    has_profile = False
    if active_profile_file.exists():
        p_name = active_profile_file.read_text(encoding="utf-8").strip()
        if (ROOT_DIR / "config" / f"settings_{p_name}.json").exists():
            has_profile = True
            print(f"[OK] 当前生效模型供应商: {p_name}")

    if not has_profile:
        print("\n[!] 提示: 尚未配置生效的模型供应商。")
        c = input("[?] 是否立即配置模型供应商？[Y/N] (默认 Y): ").strip().lower()
        if c in ("", "y"):
            run_cmd([sys.executable, str(ROOT_DIR / "scripts" / "manage_models.py")])


def setup_feishu(mode: str):
    """直接使用 npm 直驱执行 auto_feishu 自动化，无中间层，杜绝二次询问与参数丢失。"""
    auto_feishu_dir = ROOT_DIR / "auto_feishu"
    if not auto_feishu_dir.exists():
        print("[ERROR] 未找到 auto_feishu 自动化目录。")
        return

    mode_label = "个人用" if mode == "personal" else "公用"
    print(f"\n[*] 准备飞书自动化配置环境（已选定: {mode_label} 模式）...")

    # 1. 确保 auto_feishu npm 依赖
    node_modules = auto_feishu_dir / "node_modules"
    if not node_modules.exists():
        print("[*] 首次配置，正在安装依赖 (npm ci)...")
        run_cmd(["npm", "ci", "--ignore-scripts"], cwd=auto_feishu_dir)

    # 2. 确保 Playwright Chromium 浏览器组件
    print("[*] 校验 Playwright Chromium 组件...")
    run_cmd(["npx", "playwright", "install", "chromium"], cwd=auto_feishu_dir)

    # 3. 按选定模式直驱飞书配置脚本
    print(f"[*] 正在执行飞书自动化配置（{mode_label}）...")
    custom_env = {"FEISHU_DEPLOY_MODE": mode}
    npm_cmd = ["npm", "run", "feishu:setup"]
    if mode == "personal":
        npm_cmd.extend(["--", "--personal"])

    code = run_cmd(npm_cmd, cwd=auto_feishu_dir, env=custom_env)
    if code == 0:
        print(f"[OK] 飞书{mode_label}模式自动化配置完成。")
    else:
        print(f"[!] 飞书自动化配置退出，返回码: {code}")


def ask_group_import():
    """飞书公用模式下的白名单导入交互。"""
    print("\n" + "=" * 46)
    print("          飞书公用模式 - 白名单配置")
    print("=" * 46)
    print(">> 默认模式：ALLOWED_USERS 保持为空，企业内全员均可直接访问。")
    print()
    choice = input("[?] 是否需要将特定群聊的所有用户ID一键导入为白名单？[Y/N] (默认 N): ").strip().lower()
    if choice == "y":
        run_cmd([sys.executable, str(ROOT_DIR / "scripts" / "import_feishu_group.py")])
    else:
        from scripts.import_feishu_group import upsert_env
        upsert_env("ALLOWED_USERS", "")
        print("[OK] 已将 ALLOWED_USERS 设为全员开放模式。")


def configure_autostart():
    """配置/管理开机自启（每次开机自动静默后台运行）。"""
    if sys.platform == "win32":
        autostart_script = ROOT_DIR / "scripts" / "setup_autostart.bat"
        if not autostart_script.exists():
            print("[!] 未找到 scripts/setup_autostart.bat。")
            return
        run_cmd(["cmd.exe", "/c", str(autostart_script)])
    else:
        autostart_script = ROOT_DIR / "scripts" / "setup_autostart_mac.sh"
        if not autostart_script.exists():
            print("[!] 未找到 scripts/setup_autostart_mac.sh。")
            return
        run_cmd(["bash", str(autostart_script)])


def main_menu():
    """主配置中心菜单。"""
    while True:
        print("\n" + "=" * 46)
        print("                 MyClaw 配置中心")
        print("=" * 46)
        print("\n【飞书接入】")
        print(" 1. 配置飞书 - 个人用（仅创建者可用，不改变应用可用范围）")
        print(" 2. 配置飞书 - 公用（可用范围全员，支持群成员一键导入白名单）")
        print(" 3. 飞书群成员一键导入白名单（日常维护工具）")
        print("\n【企业微信接入】")
        print(" 4. 配置企业微信（长连接自建应用 + 连通实测）")
        print("\n【组合与模型管理】")
        print(" 5. 完整配置（飞书公用 + 企业微信）")
        print(" 6. 模型与供应商管理（切换/添加/修改/测试模型）")
        print("\n【系统】")
        print(" 7. 配置开机自启（每次开机自动静默后台运行）")
        print("\n【退出】")
        print(" 0. 退出向导（完成并显示启动说明）")
        print()

        try:
            choice = input("请选择 [0/1/2/3/4/5/6/7]: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n已退出。")
            break

        if choice == "1":
            setup_feishu("personal")
        elif choice == "2":
            setup_feishu("public")
            ask_group_import()
        elif choice == "3":
            run_cmd([sys.executable, str(ROOT_DIR / "scripts" / "import_feishu_group.py")])
        elif choice == "4":
            run_cmd([sys.executable, str(ROOT_DIR / "scripts" / "setup_wecom.py")])
        elif choice == "5":
            setup_feishu("public")
            ask_group_import()
            print("\n[*] 接下来进入企业微信配置...")
            run_cmd([sys.executable, str(ROOT_DIR / "scripts" / "setup_wecom.py")])
        elif choice == "6":
            run_cmd([sys.executable, str(ROOT_DIR / "scripts" / "manage_models.py")])
        elif choice == "7":
            configure_autostart()
        elif choice in ("0", "q", "exit"):
            finish_setup()
            break
        else:
            print("[!] 无效选项，请重新输入。")


def finish_setup():
    """完成向导并展示运行说明。"""
    print("\n" + "=" * 50)
    print("[SUCCESS] MyClaw 安装与配置完成！")
    print("=" * 50)
    if sys.platform == "win32":
        print("  启动服务      : 双击 launcher_windows\\MyClaw.bat（重启用 launcher_windows\\MyClaw-Restart.bat）")
        print("  重新配置      : 双击 launcher_windows\\MyClaw-Setup.bat 重跑向导")
    else:
        print("  启动服务      : 双击 launcher_macos/MyClaw.command（或运行 bash scripts/restart_mac.sh）")
        print("  重新配置      : 双击 launcher_macos/MyClaw-Setup.command 重跑向导")
    print("  开机自启      : 配置中心选 7")
    print("  健康检查      : curl http://127.0.0.1:8080/health")
    print("=" * 50)


if __name__ == "__main__":
    os.chdir(ROOT_DIR)
    check_prerequisites()
    main_menu()
