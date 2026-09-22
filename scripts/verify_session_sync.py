"""双端会话同步诊断与自动化验证工具。

用于验证：
1. 当前工作区下的 Claude 会话文件状态（entrypoint 是否规范化为 cli）；
2. ~/.claude/history.jsonl 历史索引记录与置顶状态；
3. 模拟飞书端自动感知电脑端最新活跃 Session 的判定结果；
4. 提供一键自愈修复（--fix）选项。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# 确保在 Windows 控制台下输出 UTF-8 不因 emoji 报错
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# 确保能正确导入 app 模块
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.agent.session_sync import (
    _claude_home,
    _encode_cwd,
    append_history_index,
    detect_cli_session_update,
    sync_claude_session_to_cli,
)


def inspect_workspace(workspace: str, auto_fix: bool = False) -> None:
    ws_path = Path(workspace).resolve()
    print("=" * 60)
    print(f"🔍 正在诊断工作区: {ws_path}")
    print("=" * 60)

    if not ws_path.is_dir():
        print(f"❌ 错误: 工作区目录不存在: {ws_path}")
        return

    claude_home = _claude_home()
    history_file = claude_home / "history.jsonl"
    print(f"📁 Claude 全局数据目录: {claude_home}")
    print(f"📄 history.jsonl 状态: {'存在' if history_file.is_file() else '❌ 不存在'}")

    enc_cwd = _encode_cwd(str(ws_path))
    project_dir = claude_home / "projects" / enc_cwd
    print(f"📁 对应 project 目录: {project_dir.name}")

    if not project_dir.is_dir():
        print(f"ℹ️ 该工作区目前尚无任何 Claude 会话记录。")
        return

    jsonl_files = sorted(project_dir.glob("*.jsonl"), key=lambda p: -p.stat().st_mtime)
    print(f"📊 发现 {len(jsonl_files)} 个会话记录文件 (按时间倒序)：\n")

    # 读取 history.jsonl 中的 sessionId 集合
    history_sids: set[str] = set()
    latest_history_sid = ""
    if history_file.is_file():
        try:
            for line in history_file.read_text("utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                    sid = row.get("sessionId")
                    if sid:
                        history_sids.add(sid)
                        latest_history_sid = sid
                except Exception:
                    pass
        except Exception as e:
            print(f"⚠️ 读取 history.jsonl 警告: {e}")

    for idx, f in enumerate(jsonl_files[:10], 1):
        sid = f.stem
        mtime_str = Path(f).stat().st_mtime
        size = f.stat().st_size

        entrypoint_status = "未知"
        first_user_text = ""
        has_sdk_cli = False
        leaf_status = "未知"
        real_latest_uuid = ""
        recorded_leaf_uuid = ""
        need_leaf_fix = False

        try:
            content = f.read_text("utf-8", errors="replace")
            if '"entrypoint":"sdk-cli"' in content or '"entrypoint": "sdk-cli"' in content:
                has_sdk_cli = True
                entrypoint_status = "❌ sdk-cli (终端可能过滤)"
            elif '"entrypoint":"cli"' in content or '"entrypoint": "cli"' in content:
                entrypoint_status = "✅ cli (终端可见)"
            else:
                entrypoint_status = "⚠️ 未标记 entrypoint"

            for line in content.splitlines():
                try:
                    row = json.loads(line)
                    t = row.get("type")
                    u = row.get("uuid")
                    if u and t in ("assistant", "user", "system"):
                        real_latest_uuid = u
                    if t == "last-prompt":
                        recorded_leaf_uuid = row.get("leafUuid") or ""
                    if t == "user" and not first_user_text:
                        msg = row.get("message", {})
                        c = msg.get("content", "")
                        if isinstance(c, str) and c.strip() and not c.startswith("<"):
                            first_user_text = c.strip()[:40]
                except Exception:
                    continue

            if not recorded_leaf_uuid:
                leaf_status = "⚠️ 缺失 last-prompt 标记"
                need_leaf_fix = True
            elif recorded_leaf_uuid == real_latest_uuid:
                leaf_status = f"✅ 已精确对齐最新节点 ({recorded_leaf_uuid[:8]})"
            else:
                leaf_status = f"❌ 指针错位！指向旧节点 {recorded_leaf_uuid[:8]} (最新真实节点: {real_latest_uuid[:8]})"
                need_leaf_fix = True
        except Exception as e:
            entrypoint_status = f"读取异常: {e}"

        in_history = "✅ 已索引" if sid in history_sids else "❌ 缺失索引"
        is_top = "🏆 终端最新置顶" if sid == latest_history_sid else ""

        print(f"[{idx}] 会话 ID: {sid}")
        print(f"    文件大小: {size} bytes | 恢复状态: {entrypoint_status}")
        print(f"    叶子指针: {leaf_status}")
        print(f"    历史登记: {in_history} {is_top}")
        print(f"    用户对话摘要: {first_user_text or '(空或纯工具)'}")

        if auto_fix and (has_sdk_cli or sid not in history_sids or need_leaf_fix):
            ok = sync_claude_session_to_cli(str(ws_path), sid, first_user_text)
            print(f"    🛠️ 已自动修复该会话（规范化 cli / 矫正 leafUuid / 置顶 history）-> {'成功' if ok else '失败'}")
        print("-" * 50)

    print("\n🤖 测试双端自愈探针 (detect_cli_session_update):")
    # 模拟飞书端当前记录为空时的感知
    probe_empty = detect_cli_session_update(str(ws_path), None)
    print(f"• 当飞书端会话为空时: 感知更新={probe_empty['has_update']} 目标Session={probe_empty['latest_session_id']}")

    if len(jsonl_files) >= 2:
        # 模拟飞书端拿着较旧的 session_id
        oldest_sid = jsonl_files[-1].stem
        probe_old = detect_cli_session_update(str(ws_path), oldest_sid)
        print(f"• 当飞书端绑定旧会话 ({oldest_sid[:8]}...) 时: 感知更新={probe_old['has_update']} 切换至={probe_old['latest_session_id'][:8]}...")


def main() -> None:
    parser = argparse.ArgumentParser(description="MyClaw 双端会话同步诊断与验证工具")
    parser.add_argument("workspace", nargs="?", default=str(REPO_ROOT), help="要诊断的工作区绝对路径")
    parser.add_argument("--fix", action="store_true", help="自动修复未规范化的 sdk-cli 会话并补全 history.jsonl")
    args = parser.parse_args()

    inspect_workspace(args.workspace, auto_fix=args.fix)


if __name__ == "__main__":
    main()
