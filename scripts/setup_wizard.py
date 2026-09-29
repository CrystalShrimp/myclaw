#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MyClaw 跨平台交互配置向导。
负责：环境自检（Node.js / Claude CLI / .env / 模型供应商）与全功能配置中心（飞书/企微/白名单/模型管理）。
"""

import os
import sys
import json
import time
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
# 以 python scripts/setup_wizard.py 方式启动时 sys.path[0] 是 scripts/ 目录，
# `from scripts.import_feishu_group import ...` 需要项目根目录在搜索路径上
sys.path.insert(0, str(ROOT_DIR))


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


def get_env_value(key: str) -> str:
    """读取 .env 中的指定配置项。"""
    env_file = ROOT_DIR / ".env"
    if not env_file.exists():
        return ""
    try:
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                if k.strip() == key:
                    return v.strip().strip("'\"")
    except Exception:
        pass
    return ""


def upsert_env_key(key: str, value: str):
    """更新或插入 .env 文件中的配置项。"""
    env_file = ROOT_DIR / ".env"
    if not env_file.exists():
        example_file = ROOT_DIR / "config" / "examples" / "env.example"
        if example_file.exists():
            shutil.copy(example_file, env_file)
        else:
            env_file.write_text(f"{key}={value}\n", encoding="utf-8")
            return

    try:
        lines = env_file.read_text(encoding="utf-8", errors="replace").splitlines()
        found = False
        new_lines = []
        for line in lines:
            stripped = line.strip()
            if not stripped.startswith("#") and "=" in stripped:
                k, _ = stripped.split("=", 1)
                if k.strip() == key:
                    new_lines.append(f"{key}={value}")
                    found = True
                    continue
            new_lines.append(line)

        if not found:
            new_lines.append(f"{key}={value}")

        env_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    except Exception as e:
        print(f"[!] 写入 .env 失败: {e}")


def get_recommended_workspace() -> Path:
    """获取默认初始运行目录：默认为当前项目所在目录。"""
    return ROOT_DIR.resolve()


def infer_claude_session_dir() -> Path:
    """自动推断电脑端 Claude Code 的历史会话存储目录。"""
    candidates: list[Path] = []
    env_cfg = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    if env_cfg:
        cfg_path = Path(env_cfg).expanduser()
        candidates.extend([cfg_path / "projects", cfg_path])

    home_claude = Path.home() / ".claude"
    candidates.extend([home_claude / "projects", home_claude])

    for cand in candidates:
        try:
            if cand.exists() and cand.is_dir():
                return cand.resolve()
        except Exception:
            continue

    return (home_claude / "projects").resolve()


def confirm_directories():
    """【Step 2/4】初始运行目录与 Claude Code 历史会话目录确认。"""
    print("\n" + "=" * 60)
    print("   【Step 2/4】初始运行目录与 Claude Code 历史会话目录确认")
    print("=" * 60)
    print()
    print(f"  MyClaw 程序运行目录 (只读): {ROOT_DIR.resolve()}")
    print()

    # 确保 .env 基础文件存在
    env_file = ROOT_DIR / ".env"
    example_file = ROOT_DIR / "config" / "examples" / "env.example"
    if not env_file.exists():
        if example_file.exists():
            shutil.copy(example_file, env_file)
            print("[OK] 已初始化生成 .env 配置文件。\n")
        else:
            env_file.touch()

    def ask_path(label: str, recommended: Path, hint: str = "") -> Path:
        print(f"  {label}")
        print(f"      当前推荐: {recommended}")
        if hint:
            print(f"      >> {hint}")
        choice = input("[?] 是否直接采用？[Y/N] (直接回车 = 采用): ").strip().lower()
        final = recommended
        if choice == "n":
            while True:
                custom = input("请输入目录绝对路径: ").strip().strip("'\"")
                if not custom:
                    print("[!] 路径不能为空，请重新输入。")
                    continue
                try:
                    final = Path(custom).expanduser().resolve()
                    break
                except Exception as e:
                    print(f"[!] 路径格式无效 ({e})，请重新输入。")
        print()
        return final

    # 1. 初始运行目录：初始任务临时在此运行，后续可用/cd 命令切换至目标目录
    configured = get_env_value("DEFAULT_WORKSPACE")
    rec_ws = Path(configured).expanduser() if configured else get_recommended_workspace()
    final_ws = ask_path(
        "[1] 初始运行目录：初始任务临时在此运行，后续可用/cd 命令切换至目标目录",
        rec_ws,
    )
    try:
        final_ws.mkdir(parents=True, exist_ok=True)
        print(f"[OK] 初始运行目录已就绪: {final_ws}")
    except Exception as e:
        print(f"[!] 创建目录遇到问题 ({e})，但已记录该路径。")
    upsert_env_key("DEFAULT_WORKSPACE", str(final_ws))

    # 2. Claude Code 历史会话目录：读取电脑端的历史会话
    sess_configured = get_env_value("CLAUDE_SESSION_DIR")
    rec_sess = Path(sess_configured).expanduser() if sess_configured else infer_claude_session_dir()
    final_sess = ask_path(
        "[2] Claude Code 历史会话目录：读取电脑端的历史会话",
        rec_sess,
    )
    try:
        final_sess.mkdir(parents=True, exist_ok=True)
        print(f"[OK] 历史会话目录已就绪: {final_sess}")
    except Exception as e:
        print(f"[!] 创建目录遇到问题 ({e})，但已记录该路径。")
    upsert_env_key("CLAUDE_SESSION_DIR", str(final_sess))


def check_environment():
    """【Step 1/4】基础运行环境检测 (Environment)。"""
    print("\n" + "=" * 60)
    print("          【Step 1/4】运行环境自检与依赖 (Environment)")
    print("=" * 60)
    print()

    # 1. 检查 Node.js
    node_path = shutil.which("node")
    if not node_path:
        print("[!] 警告: 未检测到 Node.js 环境（auto_feishu 需要 Node.js v20+）。")
        print("    建议前往 https://nodejs.org 下载安装 Node.js LTS 版本。")
    else:
        try:
            ver = subprocess.check_output(["node", "-v"], text=True).strip()
            print(f"[OK] Node.js 运行时环境: {ver}")
        except Exception:
            print("[OK] 检测到 Node.js 运行时。")

    # 2. 检查 Claude Code CLI
    claude_path = shutil.which("claude")
    if not claude_path:
        print("[!] 提示: 未检测到全局 Claude Code CLI。")
        choice = input("[?] 是否立即通过国内镜像全局安装 Claude CLI？[Y/N] (默认 N): ").strip().lower()
        if choice == "y":
            print("[*] 正在安装 @anthropic-ai/claude-code ...")
            run_cmd(["npm", "install", "-g", "@anthropic-ai/claude-code", "--registry=https://registry.npmmirror.com"])
    else:
        print("[OK] Claude Code CLI 已就绪。")


def configure_initial_preferences():
    """交互式配置初始运行偏好：Provider / Model / Effort / Mode，并写入 config/global_preferences.json。"""
    from app.profiles import discover_profiles, get_active_profile, profile_level_models
    from app.state.preferences import UserPreferences, preferences_manager

    current = preferences_manager.get_global()
    profiles = discover_profiles()
    active_prof = get_active_profile() or current.model or (next(iter(profiles.keys())) if profiles else "glm")

    print("\n" + "-" * 60)
    print("  【初始运行偏好配置】(Provider / Model / Effort / Mode)")
    print("-" * 60)

    # 1. Provider
    if profiles:
        prof_keys = list(profiles.keys())
        default_prof = active_prof if active_prof in prof_keys else prof_keys[0]
        print(f"\n  [1/4] 选择默认供应商 (Provider) [当前默认: {default_prof}]:")
        for idx, k in enumerate(prof_keys, 1):
            p = profiles[k]
            label = p.get("label", k) if isinstance(p, dict) else getattr(p, "label", k)
            mark = " (默认)" if k == default_prof else ""
            print(f"    {idx}. {k} - {label}{mark}")
        raw_p = input(f"  请选择序号或名称 (直接回车 = {default_prof}): ").strip()
        chosen_provider = default_prof
        if raw_p:
            if raw_p.isdigit() and 1 <= int(raw_p) <= len(prof_keys):
                chosen_provider = prof_keys[int(raw_p) - 1]
            elif raw_p in profiles:
                chosen_provider = raw_p
    else:
        chosen_provider = active_prof

    # 2. Model（若当前供应商为已知模型，直接显示具体模型名称而非 sonnet/opus/haiku）
    models_map = profile_level_models(chosen_provider)
    raw_levels = [
        ("sonnet", "标准/推荐"),
        ("opus", "旗舰/最强"),
        ("haiku", "轻量/快速"),
    ]
    level_options: list[tuple[str, str, str]] = []
    seen_disp: set[str] = set()
    for code, desc in raw_levels:
        actual = models_map.get(code, "")
        disp = actual or code
        if disp in seen_disp:
            continue
        seen_disp.add(disp)
        level_options.append((code, disp, desc))

    default_level = current.level if any(c == current.level for c, _, _ in level_options) else level_options[0][0]
    default_level_disp = next((disp for c, disp, _ in level_options if c == default_level), default_level)
    print(f"\n  [2/4] 选择默认模型 (Model) [当前默认: {default_level_disp}]:")
    for idx, (code, disp, desc) in enumerate(level_options, 1):
        mark = " (默认)" if code == default_level else ""
        print(f"    {idx}. {disp} - {desc}{mark}")
    raw_l = input(f"  请选择 [1-{len(level_options)}] 或模型名称 (直接回车 = {default_level_disp}): ").strip().lower()
    chosen_level = default_level
    if raw_l:
        if raw_l.isdigit() and 1 <= int(raw_l) <= len(level_options):
            chosen_level = level_options[int(raw_l) - 1][0]
        else:
            for code, disp, _ in level_options:
                if raw_l in (code.lower(), disp.lower()):
                    chosen_level = code
                    break
    chosen_level_disp = next((disp for c, disp, _ in level_options if c == chosen_level), chosen_level)

    # 3. Effort (low / medium / high / xhigh / max)
    effort_options = [
        ("medium", "Medium（默认平衡）"),
        ("high", "High（深度思考）"),
        ("xhigh", "XHigh（超强推理）"),
        ("max", "Max（极限思考）"),
        ("low", "Low（快速响应）"),
    ]
    valid_efforts = tuple(k for k, _ in effort_options)
    default_effort = current.effort if current.effort in valid_efforts else "medium"
    print(f"\n  [3/4] 选择默认思考力度 (Effort) [当前默认: {default_effort}]:")
    for idx, (val, desc) in enumerate(effort_options, 1):
        mark = " (默认)" if val == default_effort else ""
        print(f"    {idx}. {val} - {desc}{mark}")
    raw_e = input(f"  请选择 [1-5] 或名称 (直接回车 = {default_effort}): ").strip().lower()
    chosen_effort = default_effort
    if raw_e:
        if raw_e.isdigit() and 1 <= int(raw_e) <= len(effort_options):
            chosen_effort = effort_options[int(raw_e) - 1][0]
        elif raw_e in valid_efforts:
            chosen_effort = raw_e

    # 4. Mode (m / h / l)
    mode_options = [
        ("m", "中权限 (m) - 读/搜索自动放行，修改代码与执行命令需确认（推荐）"),
        ("h", "高权限 (h) - 全自动执行，无需人工确认"),
        ("l", "低权限 (l) - 所有工具调用均需人工确认"),
    ]
    default_mode = current.mode if current.mode in ("m", "h", "l") else "m"
    print(f"\n  [4/4] 选择默认权限审批模式 (Mode) [当前默认: {default_mode}]:")
    for idx, (val, desc) in enumerate(mode_options, 1):
        mark = " (默认)" if val == default_mode else ""
        print(f"    {idx}. {desc}{mark}")
    raw_m = input(f"  请选择 [1-3] 或 m/h/l (直接回车 = {default_mode}): ").strip().lower()
    chosen_mode = default_mode
    if raw_m:
        if raw_m.isdigit() and 1 <= int(raw_m) <= len(mode_options):
            chosen_mode = mode_options[int(raw_m) - 1][0]
        elif raw_m in ("m", "h", "l"):
            chosen_mode = raw_m

    prefs = UserPreferences(
        model=chosen_provider,
        level=chosen_level,
        mode=chosen_mode,
        effort=chosen_effort,
    )
    preferences_manager.save_global(prefs)
    print(
        f"\n[OK] 初始运行配置已保存: Provider={chosen_provider} | Model={chosen_level_disp} "
        f"| Effort={chosen_effort} | Mode={chosen_mode}"
    )


def check_or_setup_models():
    """【Step 3/4】模型供应商与初始偏好配置 (Provider / Model / Effort / Mode)。"""
    print("\n" + "=" * 60)
    print("  【Step 3/4】模型供应商与初始运行配置 (Provider/Model/Effort/Mode)")
    print("=" * 60)
    print()

    active_profile_file = ROOT_DIR / "config" / "active_profile"
    has_profile = False
    current_model = ""
    if active_profile_file.exists():
        p_name = active_profile_file.read_text(encoding="utf-8").strip()
        setting_file = ROOT_DIR / "config" / f"settings_{p_name}.json"
        if setting_file.exists():
            has_profile = True
            current_model = p_name

    if has_profile:
        print(f"[OK] 当前生效模型供应商: {current_model}（已配置 API Key）")
        c = input("[?] 是否需要调整或重新配置模型供应商 API Key？[y/N] (直接回车 = 保持当前): ").strip().lower()
        if c == "y":
            run_cmd([sys.executable, str(ROOT_DIR / "scripts" / "manage_models.py")])
    else:
        print("[!] 提示: 尚未配置生效的模型供应商（如智谱 GLM、DeepSeek、Kimi、Claude 官方等）。")
        c = input("[?] 是否立即配置模型供应商 API Key？[Y/N] (直接回车 = 是): ").strip().lower()
        if c in ("", "y"):
            run_cmd([sys.executable, str(ROOT_DIR / "scripts" / "manage_models.py")])

    configure_initial_preferences()


def ensure_playwright_chromium(auto_feishu_dir: Path) -> bool:
    """快速检测 Playwright Chromium 驱动，已就绪则秒级跳过，未就绪则镜像加速安装。"""
    # 1. 尝试检测 Chromium 可执行文件是否已存在
    check_script = "const { chromium } = require('playwright'); const p = chromium.executablePath(); process.exit(require('fs').existsSync(p) ? 0 : 1);"
    try:
        res = subprocess.run(["node", "-e", check_script], cwd=auto_feishu_dir, capture_output=True, text=True)
        if res.returncode == 0:
            print("[OK] Playwright Chromium 浏览器驱动已就绪。")
            return True
    except Exception:
        pass

    # 2. 若未就绪，清理潜在的残留死锁目录 __dirlock
    try:
        cache_dirs = []
        if sys.platform == "darwin":
            cache_dirs.append(Path.home() / "Library" / "Caches" / "ms-playwright")
        elif sys.platform == "win32":
            local_appdata = os.environ.get("LOCALAPPDATA")
            if local_appdata:
                cache_dirs.append(Path(local_appdata) / "ms-playwright")
        else:
            cache_dirs.append(Path.home() / ".cache" / "ms-playwright")

        for cd in cache_dirs:
            dirlock = cd / "__dirlock"
            if dirlock.exists():
                shutil.rmtree(dirlock, ignore_errors=True)
    except Exception:
        pass

    # 3. 注入国内镜像加速执行安装
    print("[*] 正在安装 Playwright Chromium 组件（已启用国内镜像加速）...")
    install_env = {
        "PLAYWRIGHT_DOWNLOAD_HOST": os.environ.get("PLAYWRIGHT_DOWNLOAD_HOST", "https://npmmirror.com/mirrors/playwright")
    }
    code = run_cmd(["npx", "playwright", "install", "chromium"], cwd=auto_feishu_dir, env=install_env)
    return code == 0


def fetch_app_creator_open_id(app_id: str, app_secret: str) -> str:
    """用应用自身凭据查创建者 open_id（application/v6/applications 的 creator_id）。

    返回的 open_id 与消息事件里发送者的 open_id 同为该应用作用域，可直接进白名单。
    """
    import urllib.request

    def call(method: str, url: str, body: dict | None = None, headers: dict | None = None) -> dict:
        import urllib.error

        h = {"Content-Type": "application/json; charset=utf-8"}
        if headers:
            h.update(headers)
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode() if body else None,
            headers=h,
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            # 飞书的 4xx 带 JSON 错误体，读出来才有 code/msg 可判
            try:
                return json.loads(e.read())
            except Exception:
                raise

    last_info: dict = {}
    for attempt in range(5):
        tok = call(
            "POST",
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            {"app_id": app_id, "app_secret": app_secret},
        ).get("tenant_access_token", "")
        # 该接口必须带 lang 参数，否则直接 400
        info = call(
            "GET",
            f"https://open.feishu.cn/open-apis/application/v6/applications/{app_id}?lang=zh_cn",
            headers={"Authorization": "Bearer " + tok},
        )
        last_info = info
        if info.get("code") == 0:
            return (info.get("data", {}).get("app", {}) or {}).get("creator_id", "")
        if info.get("code") == 99991672 and attempt < 4:
            print(f"[*] 等待飞书应用权限(application:application:self_manage)同步生效 ({attempt + 1}/5)...")
            time.sleep(3)
            continue
        break

    raise RuntimeError(f"code={last_info.get('code')} {last_info.get('msg')}")


def apply_personal_allowlist():
    """个人用模式收尾：把应用创建者的 open_id 写入 ALLOWED_USERS（名单里明确只放本人）。"""
    app_id = get_env_value("FEISHU_APP_ID")
    app_secret = get_env_value("FEISHU_APP_SECRET")
    if not app_id or not app_secret or app_id.startswith("cli_x"):
        print("[!] .env 中缺少有效的飞书凭据，未改动 ALLOWED_USERS。")
        return

    try:
        creator = fetch_app_creator_open_id(app_id, app_secret)
    except Exception as e:
        print(f"[!] 查询应用创建者失败（{e}），未改动 ALLOWED_USERS。")
        return
    if not creator:
        print("[!] 未获取到应用创建者 open_id，未改动 ALLOWED_USERS。")
        return

    from scripts.import_feishu_group import upsert_env
    upsert_env("ALLOWED_USERS", creator)
    print(f"[OK] 个人用模式：ALLOWED_USERS 已写入应用创建者 {creator}（名单里明确仅本人可用）。")
    print("     若服务正在运行，请重启服务使配置生效。")


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
    playwright_pkg = node_modules / "playwright"
    if not node_modules.exists() or not playwright_pkg.exists():
        print("[*] 依赖缺失或未完全安装，正在安装依赖 (npm install)...")
        run_cmd(["npm", "install", "--no-audit", "--no-fund"], cwd=auto_feishu_dir)

    # 2. 确保 Playwright Chromium 浏览器组件（已就绪秒级跳过，未就绪镜像加速）
    ensure_playwright_chromium(auto_feishu_dir)

    # 3. 按选定模式直驱飞书配置脚本
    print(f"[*] 正在执行飞书自动化配置（{mode_label}）...")
    custom_env = {"FEISHU_DEPLOY_MODE": mode}
    npm_cmd = ["npm", "run", "feishu:setup"]
    if mode == "personal":
        npm_cmd.extend(["--", "--personal"])

    code = run_cmd(npm_cmd, cwd=auto_feishu_dir, env=custom_env)
    if code == 0:
        print(f"[OK] 飞书{mode_label}模式自动化配置完成。")
        if mode == "personal":
            apply_personal_allowlist()
    else:
        print(f"[!] 飞书自动化配置退出，返回码: {code}")


def setup_wecom_auto():
    """企业微信自动化配置：auto_wecom 浏览器自动化（扫码登录→复用/创建机器人→读凭据）+ WS 长连接实测。"""
    auto_dir = ROOT_DIR / "auto_wecom"
    if not auto_dir.exists():
        print("[ERROR] 未找到 auto_wecom 自动化目录。")
        return

    node_modules = auto_dir / "node_modules"
    if not (node_modules / "playwright").exists():
        print("[*] 依赖缺失，正在安装依赖 (npm install)...")
        run_cmd(["npm", "install", "--no-audit", "--no-fund"], cwd=auto_dir)
    ensure_playwright_chromium(auto_dir)

    print("[*] 正在执行企业微信自动化配置（会打开浏览器，需用企业微信 App 扫码登录管理后台）...")
    code = run_cmd(["npm", "run", "wecom:setup"], cwd=auto_dir)
    if code != 0:
        print(f"[!] 企业微信自动化配置退出，返回码: {code}")
        return

    # WS 长连接实测（复用 setup_wecom.py 的 test_connection；会短暂挤掉旧连接，服务自动重连）
    bot_id = get_env_value("WECOM_BOT_ID")
    secret = get_env_value("WECOM_SECRET")
    if bot_id and secret:
        import asyncio

        from scripts.setup_wecom import test_connection
        print("[*] 正在实测长连接（订阅 + 心跳）...")
        ok, detail = asyncio.run(test_connection(bot_id, secret))
        if ok:
            print(f"[OK] 长连接实测通过：{detail}")
            print("     若服务正在运行，请重启服务使企微通道生效，然后在企业微信里给机器人发 /help 验证。")
        else:
            print(f"[!] 长连接实测失败：{detail}（请核对机器人详情页配置方式为长连接）")
    else:
        print("[!] 未在 .env 中检测到 WECOM_BOT_ID/WECOM_SECRET，跳过长连接实测。")


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


def collect_init_status() -> dict:
    """收集前三步初始化状态（运行环境 / 目录 / 模型供应商与初始偏好）。"""
    from app.state.preferences import preferences_manager

    workspace = get_env_value("DEFAULT_WORKSPACE")
    session_dir = get_env_value("CLAUDE_SESSION_DIR")

    node_version = ""
    if shutil.which("node"):
        try:
            node_version = subprocess.check_output(["node", "-v"], text=True).strip()
        except Exception:
            node_version = "(已安装)"
    claude_ready = shutil.which("claude") is not None

    active_profile = ""
    active_profile_file = ROOT_DIR / "config" / "active_profile"
    if active_profile_file.exists():
        p_name = active_profile_file.read_text(encoding="utf-8", errors="replace").strip()
        if p_name and (ROOT_DIR / "config" / f"settings_{p_name}.json").exists():
            active_profile = p_name

    prefs = preferences_manager.get_global()

    return {
        "workspace": workspace,
        "session_dir": session_dir,
        "workspace_done": bool(workspace.strip()) and bool(session_dir.strip()),
        "node_version": node_version,
        "claude_ready": claude_ready,
        "environment_done": bool(node_version) and claude_ready,
        "model_profile": active_profile,
        "pref_provider": prefs.model,
        "pref_level": prefs.level,
        "pref_effort": prefs.effort,
        "pref_mode": prefs.mode,
        "prefs_complete": prefs.complete,
        "model_done": bool(active_profile) and prefs.complete,
    }


def run_init_steps(force: bool = False):
    """执行前三步初始化。force=True 全部重跑；否则只补跑未完成的步骤。"""
    status = collect_init_status()
    steps = [
        ("environment", "运行环境", check_environment),
        ("workspace", "运行目录与会话目录", confirm_directories),
        ("model", "模型供应商与初始配置", check_or_setup_models),
    ]
    for key, label, fn in steps:
        if force or not status[f"{key}_done"]:
            fn()
        else:
            print(f"[OK] {label}已完成初始化，跳过。")


def show_init_config():
    """查看前三步初始化配置信息，可选择重置并重跑。"""
    from app.profiles import profile_level_models
    from app.state.preferences import preferences_manager

    status = collect_init_status()
    prov = status["pref_provider"] or status["model_profile"] or ""
    lvl = status["pref_level"] or ""
    actual_model = profile_level_models(prov).get(lvl, lvl) if (prov and lvl) else lvl
    print("\n" + "=" * 60)
    print("        初始化配置信息（Step 1-3 初始化设置）")
    print("=" * 60)
    print()
    print("  【Step 1 运行环境】")
    print(f"    Node.js         : {status['node_version'] or '未检测到（auto_feishu 需要 v20+）'}")
    print(f"    Claude Code CLI : {'已就绪' if status['claude_ready'] else '未检测到'}")
    print("  【Step 2 目录】")
    print(f"    初始运行目录 (DEFAULT_WORKSPACE)  : {status['workspace'] or '(未配置)'}")
    print(f"    历史会话目录 (CLAUDE_SESSION_DIR) : {status['session_dir'] or '(未配置，自动读取 ~/.claude/projects)'}")
    print("  【Step 3 模型供应商与初始运行配置】")
    print(f"    当前生效供应商 (Provider) : {prov or '(未配置)'}")
    print(f"    默认模型       (Model)    : {actual_model or '(未配置)'}")
    print(f"    默认思考力度   (Effort)   : {status['pref_effort'] or '(未配置)'}")
    print(f"    默认审批模式   (Mode)     : {status['pref_mode'] or '(未配置)'}")
    print()

    if status["workspace_done"] and status["environment_done"] and status["model_done"]:
        print("  [OK] 初始化设置已全部完成。")
    else:
        print("  [!] 存在未完成的初始化项。")
    choice = input("[?] 是否重置并重新运行初始化设置（Step 1-3）？[y/N] (直接回车 = 否): ").strip().lower()
    if choice == "y":
        preferences_manager.clear("")
        run_init_steps(force=True)


def main_menu():
    """【Step 4/4】消息平台接入与配置中心。"""
    while True:
        print("\n" + "=" * 60)
        print("    【Step 4/4】消息平台接入与配置中心 (Platform Access)")
        print("=" * 60)
        print("\n【飞书接入】")
        print(" 1. 配置飞书 - 个人：仅创建者可用")
        print(" 2. 配置飞书 - 公用：全部成员可用")
        print(" 3. └─ 一键授权飞书群成员：基于2，限制仅特定群成员可用")
        print("\n【企业微信接入】")
        print(" 4. 配置企业微信")
        print(" 5. 完整配置（飞书公用 + 企业微信）")
        print("\n【模型供应商】")
        print(" 6. 模型供应商与初始运行配置（API Key / Provider / Model / Effort / Mode）")
        print("\n【系统】")
        print(" 7. 配置开机自启（每次开机自动静默后台运行）")
        print(" 8. 查看/重置初始化配置（工作空间/运行环境/模型与初始偏好）")
        print("\n【退出】")
        print(" 0. 退出向导（完成并显示启动说明）")
        print()

        try:
            choice = input("请选择 [0-8]: ").strip()
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
            setup_wecom_auto()
        elif choice == "5":
            setup_feishu("public")
            ask_group_import()
            print("\n[*] 接下来进入企业微信配置...")
            setup_wecom_auto()
        elif choice == "6":
            run_cmd([sys.executable, str(ROOT_DIR / "scripts" / "manage_models.py")])
            configure_initial_preferences()
        elif choice == "7":
            configure_autostart()
        elif choice == "8":
            show_init_config()
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
    # 前三步为一次性初始化：已完成则跳过，直接进入 Step 4 配置中心
    status = collect_init_status()
    if status["workspace_done"] and status["environment_done"] and status["model_done"]:
        print("[OK] 初始化设置已完成（工作空间 / 运行环境 / 模型供应商），跳过 Step 1-3。")
        print("     （如需查看或重新配置初始化项：配置中心选 8）")
    else:
        run_init_steps()
    main_menu()

