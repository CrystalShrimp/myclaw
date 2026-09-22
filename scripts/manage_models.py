"""MyClaw 模型与供应商配置管理向导 (Independent Model Management Wizard)

支持随时独立运行：
  1. 查看所有供应商与切换当前生效模型
  2. 添加新供应商（Claude 官方、DeepSeek、智谱 GLM、Kimi、自定义等）
  3. 修改已有供应商配置（API Key、请求地址、模型档位）
  4. 测试模型连通性（向端点发送最简请求）
  5. 删除供应商配置
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.profiles import (
    CONFIG_DIR,
    ACTIVE_PROFILE_FILE,
    discover_profiles,
    get_active_profile,
    switch_profile,
    test_profile,
    load_profile_env,
)
from scripts.setup_provider import (
    PRESETS,
    configure_preset,
    configure_custom,
    verify_key,
    write_profile,
    _input,
    _ask_required,
    _ask_key,
)


def print_header(title: str) -> None:
    print("\n" + "=" * 50)
    print(f"       {title}")
    print("=" * 50)


def get_profile_list() -> list[str]:
    profiles = discover_profiles()
    return sorted(profiles.keys())


def show_profiles_summary() -> list[str]:
    names = get_profile_list()
    active = get_active_profile()
    if not names:
        print("  [提示] 当前尚未配置任何模型供应商。")
        return []

    print("\n当前已配置的供应商列表：")
    for idx, name in enumerate(names, 1):
        is_active = (name == active)
        marker = " ★ [当前生效]" if is_active else ""
        env = load_profile_env(name)
        base = env.get("ANTHROPIC_BASE_URL", "")
        if name == "claude" and not base:
            base = "https://api.anthropic.com (官方默认)"
        has_token = bool(env.get("ANTHROPIC_AUTH_TOKEN"))
        auth_desc = "API Key 认证" if has_token else ("本机官方登录态" if name == "claude" else "未设 Token")
        m_haiku = env.get("ANTHROPIC_DEFAULT_HAIKU_MODEL", "-")
        m_sonnet = env.get("ANTHROPIC_DEFAULT_SONNET_MODEL", "-")
        m_opus = env.get("ANTHROPIC_DEFAULT_OPUS_MODEL", "-")

        print(f"  {idx}. [{name}]{marker}")
        print(f"     认证方式: {auth_desc}")
        if base:
            print(f"     请求地址: {base}")
        print(f"     模型档位: haiku={m_haiku} | sonnet={m_sonnet} | opus={m_opus}")
    return names


def menu_switch_active() -> None:
    names = show_profiles_summary()
    if not names:
        return
    print()
    choice = _input(f"请输入要设为生效的编号 [1-{len(names)}] (回车取消): ")
    if not choice or not choice.isdigit():
        return
    idx = int(choice) - 1
    if 0 <= idx < len(names):
        target = names[idx]
        if switch_profile(target):
            print(f"[OK] 成功切换生效模型为: {target}")
            # 自动进行连通性轻测
            print("正在测试新模型连通性...")
            ok, msg = test_profile(target)
            if ok:
                print(f"[OK] 连通性测试通过: {msg}")
            else:
                print(f"[!] 连通性测试告警: {msg}")
        else:
            print(f"[X] 切换失败: {target}")
    else:
        print("[X] 无效的编号。")


def menu_add_provider() -> None:
    print_header("添加新供应商 / 模型")
    print("可选预设：")
    for k, p in PRESETS.items():
        print(f"  {k}. {p['label']}  ({p['site']})")
    print("  5. 自定义供应商（手填 名称/地址/Key/三档模型）")
    print("  0. 返回上级")

    choice = _input("请选择编号 [0-5]: ")
    ok = False
    new_name = ""
    if choice in PRESETS:
        preset = PRESETS[choice]
        new_name = preset["name"]
        ok = configure_preset(preset)
    elif choice == "5":
        ok = configure_custom()
    elif choice in ("0", ""):
        return
    else:
        print("[X] 无效选择。")
        return

    if ok:
        print("\n[OK] 供应商配置已保存！")
        # 询问是否切换为当前生效
        names = get_profile_list()
        if names:
            ask_switch = _input("[?] 是否立即将该供应商设为当前生效模型？[Y/n]: ")
            if ask_switch.lower() in ("y", ""):
                target_name = new_name if new_name else names[-1]
                switch_profile(target_name)
                print(f"[OK] 已将 {target_name} 设为当前生效模型。")


def menu_edit_provider() -> None:
    print_header("修改已有供应商配置")
    names = get_profile_list()
    if not names:
        print("当前没有任何供应商配置可修改。")
        return

    for idx, name in enumerate(names, 1):
        print(f"  {idx}. {name}")
    choice = _input(f"请选择要修改的供应商编号 [1-{len(names)}] (回车取消): ")
    if not choice or not choice.isdigit():
        return
    idx = int(choice) - 1
    if not (0 <= idx < len(names)):
        print("[X] 无效的编号。")
        return

    name = names[idx]
    file_path = CONFIG_DIR / f"settings_{name}.json"
    if not file_path.exists():
        print(f"[X] 配置文件不存在: {file_path}")
        return

    data = json.loads(file_path.read_text("utf-8"))
    env = data.setdefault("env", {})

    print(f"\n正在修改 [{name}] 配置 (直接回车表示保持原值):")

    curr_token = env.get("ANTHROPIC_AUTH_TOKEN", "")
    masked_token = (curr_token[:6] + "..." + curr_token[-4:]) if len(curr_token) > 12 else (curr_token or "未设置")
    new_token = _input(f"API Key [当前: {masked_token}] (回车保持): ")
    if new_token:
        env["ANTHROPIC_AUTH_TOKEN"] = new_token

    curr_base = env.get("ANTHROPIC_BASE_URL", "")
    new_base = _input(f"Base URL [当前: {curr_base or '默认'}] (回车保持): ")
    if new_base:
        env["ANTHROPIC_BASE_URL"] = new_base

    curr_h = env.get("ANTHROPIC_DEFAULT_HAIKU_MODEL", "")
    new_h = _input(f"Haiku 档模型 [当前: {curr_h}] (回车保持): ")
    if new_h:
        env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = new_h

    curr_s = env.get("ANTHROPIC_DEFAULT_SONNET_MODEL", "")
    new_s = _input(f"Sonnet 档模型 [当前: {curr_s}] (回车保持): ")
    if new_s:
        env["ANTHROPIC_DEFAULT_SONNET_MODEL"] = new_s

    curr_o = env.get("ANTHROPIC_DEFAULT_OPUS_MODEL", "")
    new_o = _input(f"Opus 档模型 [当前: {curr_o}] (回车保持): ")
    if new_o:
        env["ANTHROPIC_DEFAULT_OPUS_MODEL"] = new_o

    curr_label = data.get("label", "")
    new_label = _input(f"显示标签 Label [当前: {curr_label or name}] (回车保持): ")
    if new_label:
        data["label"] = new_label

    write_profile(name, data)
    print(f"[OK] [{name}] 配置已成功更新！")

    test_now = _input("[?] 是否立即测试该配置连通性？[Y/n]: ")
    if test_now.lower() in ("y", ""):
        ok, msg = test_profile(name)
        if ok:
            print(f"[OK] 连通性测试通过: {msg}")
        else:
            print(f"[!] 连通性测试未通过: {msg}")


def menu_test_connectivity() -> None:
    print_header("测试模型连通性")
    names = get_profile_list()
    if not names:
        print("当前没有任何供应商配置可测试。")
        return
    active = get_active_profile()

    print(f"当前生效模型: {active}")
    print("可选列表：")
    for idx, name in enumerate(names, 1):
        marker = " (当前生效)" if name == active else ""
        print(f"  {idx}. {name}{marker}")

    choice = _input(f"请选择要测试的编号 [1-{len(names)}] (回车默认当前生效): ")
    target = active
    if choice and choice.isdigit():
        idx = int(choice) - 1
        if 0 <= idx < len(names):
            target = names[idx]
        else:
            print("[X] 无效编号。")
            return

    print(f"正在向 [{target}] 发送轻量级握手测试请求...")
    ok, msg = test_profile(target)
    if ok:
        print(f"[OK] 测试成功: {msg}")
    else:
        print(f"[X] 测试失败: {msg}")


def menu_delete_provider() -> None:
    print_header("删除供应商配置")
    names = get_profile_list()
    active = get_active_profile()
    if not names:
        print("当前没有任何供应商配置。")
        return

    print("已配置的供应商：")
    for idx, name in enumerate(names, 1):
        marker = " ★ [当前生效-不可删除]" if name == active else ""
        print(f"  {idx}. {name}{marker}")

    choice = _input(f"请选择要删除的供应商编号 [1-{len(names)}] (回车取消): ")
    if not choice or not choice.isdigit():
        return
    idx = int(choice) - 1
    if not (0 <= idx < len(names)):
        print("[X] 无效编号。")
        return

    target = names[idx]
    if target == active:
        print(f"[X] 不能删除当前正在生效的供应商 [{target}]！请先将其切换为其他供应商后再删除。")
        return

    confirm = _input(f"[!] 确认永久删除配置 [{target}] 吗？[y/N]: ")
    if confirm.lower() == "y":
        target_file = CONFIG_DIR / f"settings_{target}.json"
        try:
            if target_file.exists():
                target_file.unlink()
            print(f"[OK] 已成功删除供应商配置: {target}")
        except Exception as e:
            print(f"[X] 删除失败: {e}")
    else:
        print("已取消删除。")


def main() -> int:
    while True:
        print_header("MyClaw 模型与供应商配置管理")
        active = get_active_profile()
        print(f"★ 当前生效模型供应商: {active}")
        print("\n功能菜单：")
        print("  1. 查看所有供应商 & 切换当前生效模型")
        print("  2. 添加新模型供应商 (Claude 官方/GLM/DeepSeek/Kimi/自定义)")
        print("  3. 修改已有供应商配置 (Key / URL / 模型档位)")
        print("  4. 测试模型端点连通性")
        print("  5. 删除已有供应商配置")
        print("  0. 退出向导")
        print()

        choice = _input("请选择操作编号 [0-5]: ")
        if choice in ("0", "q", "exit", "\x00EOF"):
            print("退出向导。")
            break
        elif choice == "1":
            menu_switch_active()
        elif choice == "2":
            menu_add_provider()
        elif choice == "3":
            menu_edit_provider()
        elif choice == "4":
            menu_test_connectivity()
        elif choice == "5":
            menu_delete_provider()
        else:
            print("[!] 无效输入，请输入 0-5。")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已退出。")
        sys.exit(0)
