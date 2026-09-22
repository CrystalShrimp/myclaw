"""Claude Code 跨平台会话双向同步与 CLI 恢复索引维护。

第一性原理：
1. Claude Code 终端交互界面的 resume picker 在检索历史会话时，依赖两个关键点：
   - ~/.claude/history.jsonl 中的项目路径与 Session ID 索引；
   - ~/.claude/projects/<enc-cwd>/<session_id>.jsonl 中首条用户消息必须标记为 `"entrypoint": "cli"`；
     若标记为 `"entrypoint": "sdk-cli"` 则会被终端 picker 主动过滤忽略。
2. 传统的 PTY-stub 机制通过后台运行虚拟终端拉起真实交互进程以骗取 `entrypoint: cli`，
   不仅强依赖 Windows 专属的 pywinpty（在 macOS 下直接崩溃），而且每次 /new 需强制休眠 10 秒，
   遇到目录信任弹窗极易挂死超时，且会向历史记录中塞入垃圾占位符。
3. 本模块采用原生文件规范注水与原子修复方案，纯 Python 标准库实现，
   0 秒等待、0 额外依赖、跨平台 100% 通用（Windows / macOS / Linux），
   确保飞书端发起的会话在电脑端交互终端中 100% 可见、可检索、可恢复。
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from pathlib import Path

logger = logging.getLogger("myclaw.session_sync")


def _claude_home() -> Path:
    return (Path.home() / ".claude").resolve()


def _encode_cwd(cwd: str) -> str:
    """与 Claude Code 的目录编码算法保持完全一致。"""
    ws_resolved = str(Path(cwd).resolve())
    return re.sub(r"[^A-Za-z0-9]", "-", ws_resolved)


def _find_session_file(workspace: str, session_id: str) -> Path | None:
    if not session_id or session_id == "__continue__":
        return None
    enc_cwd = _encode_cwd(workspace)
    project_dir = _claude_home() / "projects" / enc_cwd
    if not project_dir.is_dir():
        return None
    f = project_dir / f"{session_id}.jsonl"
    return f if f.is_file() else None


def append_history_index(workspace: str, session_id: str, display_text: str = "") -> None:
    """以标准格式向 ~/.claude/history.jsonl 注册或更新索引（移至末尾，确保在 resume 菜单中置顶）。"""
    if not session_id or session_id == "__continue__":
        return
    claude_home = _claude_home()
    claude_home.mkdir(parents=True, exist_ok=True)
    history_file = claude_home / "history.jsonl"
    ws_path = str(Path(workspace).resolve()).rstrip("\\/")

    existing_lines: list[str] = []
    if history_file.is_file():
        try:
            for line in history_file.read_text("utf-8", errors="replace").splitlines():
                line_str = line.strip()
                if not line_str:
                    continue
                try:
                    row = json.loads(line_str)
                    # 过滤掉同 sessionId 的旧条目（实现去重并置顶到末尾）
                    if row.get("sessionId") == session_id:
                        continue
                except Exception:
                    pass
                existing_lines.append(line_str)
        except Exception as e:
            logger.warning("Failed to read history.jsonl: %s", e)

    new_row = {
        "display": display_text[:200] if display_text else "MyClaw Session",
        "pastedContents": {},
        "timestamp": int(time.time() * 1000),
        "project": ws_path,
        "sessionId": session_id,
    }
    existing_lines.append(json.dumps(new_row, ensure_ascii=False))

    try:
        tmp_file = history_file.with_suffix(".tmp")
        tmp_file.write_text("\n".join(existing_lines) + "\n", "utf-8")
        tmp_file.replace(history_file)
        logger.info("Updated history.jsonl index for session %s (%s)", session_id, display_text[:30])
    except Exception as e:
        logger.warning("Failed to update history.jsonl: %s", e)


def sync_claude_session_to_cli(workspace: str, session_id: str, prompt: str = "") -> bool:
    """原子修复 session 文件中的 entrypoint 标记，校准 leafUuid，并同步更新 history 索引。

    第一性原理：
    Claude Code 本地交互终端（claude / claude -r）在恢复历史时，严格依赖两点：
    1. entrypoint 不能为 sdk-cli（否则被终端静默过滤）；
    2. 文件末尾的 `type: "last-prompt"` 必须携带正确的 `leafUuid`（叶子节点指针），
       终端以此指针为起点向上回溯构建历史；若指针停留在旧分支，飞书发送的新消息将被完全旁路。
    本函数在每次任务完成时执行消息树校准，确保电脑终端交互恢复时 100% 完整展示全部上下文。
    """
    if not session_id or session_id == "__continue__":
        return False

    session_file = _find_session_file(workspace, session_id)
    if not session_file or not session_file.is_file():
        return False

    try:
        raw_content = session_file.read_text("utf-8", errors="replace")
        lines = raw_content.splitlines()

        kept_lines: list[str] = []
        latest_uuid = ""
        last_user_text = prompt.strip() if prompt else ""

        for line in lines:
            line_str = line.strip()
            if not line_str:
                continue
            try:
                row = json.loads(line_str)
                # 剔除旧的、位置过时的 last-prompt 标记行（稍后在文件末尾写出最新的）
                if row.get("type") == "last-prompt":
                    continue

                # 记录最新的有效节点 uuid 作为叶子节点候选
                node_uuid = row.get("uuid")
                node_type = row.get("type")
                if node_uuid and node_type in ("assistant", "user", "system"):
                    latest_uuid = node_uuid

                # 若未传 prompt，提取最后一条真实用户提问作为 lastPrompt
                if not last_user_text and node_type == "user":
                    msg = row.get("message", {})
                    c = msg.get("content", "")
                    if isinstance(c, str) and c.strip() and not c.startswith("<"):
                        last_user_text = c.strip()
            except Exception:
                pass

            kept_lines.append(line_str)

        if not last_user_text:
            last_user_text = "MyClaw 对话"

        # 如果找出了最新的叶子节点，构造并追加规范的 last-prompt 记录
        if latest_uuid:
            last_prompt_row = {
                "type": "last-prompt",
                "lastPrompt": last_user_text[:200],
                "leafUuid": latest_uuid,
                "sessionId": session_id,
            }
            kept_lines.append(json.dumps(last_prompt_row, ensure_ascii=False))

        # 全局安全替换所有 entrypoint: sdk-cli 为 cli
        merged_content = "\n".join(kept_lines) + "\n"
        if '"entrypoint":"sdk-cli"' in merged_content or '"entrypoint": "sdk-cli"' in merged_content:
            merged_content = re.sub(r'"entrypoint"\s*:\s*"sdk-cli"', '"entrypoint":"cli"', merged_content)

        session_file.write_text(merged_content, "utf-8")
        logger.info(
            "Normalized session %s: leafUuid=%s, prompt=%s",
            session_id, latest_uuid[:8] if latest_uuid else "(none)", last_user_text[:30],
        )
    except Exception as e:
        logger.warning("Failed to normalize session file %s: %s", session_file, e)

    # 同步更新 ~/.claude/history.jsonl
    append_history_index(workspace, session_id, last_user_text or prompt)
    return True


def create_cli_born_session(workspace: str) -> str:
    """原生生成一个可在电脑端终端直接看到的 Claude 会话 ID。

    纯文件规范写入，无需启动任何虚假终端进程，
    彻底摆脱 winpty 依赖，在 macOS 和 Windows 上均可毫秒级完成。
    """
    session_id = str(uuid.uuid4())
    ws_resolved = str(Path(workspace).resolve())
    enc_cwd = _encode_cwd(workspace)
    project_dir = _claude_home() / "projects" / enc_cwd
    project_dir.mkdir(parents=True, exist_ok=True)

    session_file = project_dir / f"{session_id}.jsonl"

    # 写入规范的初始会话前置元数据记录（entrypoint 标为 cli）
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    msg_uuid = str(uuid.uuid4())
    prompt_id = str(uuid.uuid4())

    records = [
        {"type": "permission-mode", "permissionMode": "default", "sessionId": session_id},
        {
            "parentUuid": None,
            "isSidechain": False,
            "promptId": prompt_id,
            "type": "user",
            "message": {"role": "user", "content": "新会话就绪"},
            "uuid": msg_uuid,
            "timestamp": now_iso,
            "permissionMode": "default",
            "userType": "external",
            "entrypoint": "cli",
            "cwd": ws_resolved,
            "sessionId": session_id,
        },
    ]

    try:
        session_file.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", "utf-8")
        append_history_index(workspace, session_id, "新会话就绪")
        logger.info("Created native cli-born session %s (0ms wait, cross-platform)", session_id)
        return session_id
    except Exception as e:
        logger.warning("Failed to create native session file: %s", e)
        # 失败时降级返回裸 UUID，后续发送消息时会由 SDK 自然创建
        return session_id


def detect_cli_session_update(workspace: str, current_session_id: str | None) -> dict:
    """检测当前工作区在电脑端本地是否有更新的 CLI 会话产生。

    若检测到本地最新会话与当前不同且修改时间晚于当前会话，返回 has_update=True 及会话信息。
    """
    res = {
        "has_update": False,
        "latest_session_id": "",
        "latest_mtime": 0.0,
        "summary": "",
    }
    if not workspace:
        return res

    enc_cwd = _encode_cwd(workspace)
    project_dir = _claude_home() / "projects" / enc_cwd
    if not project_dir.is_dir():
        return res

    # 查当前会话的修改时间
    cur_mtime = 0.0
    if current_session_id and current_session_id != "__continue__":
        cur_file = project_dir / f"{current_session_id}.jsonl"
        if cur_file.is_file():
            try:
                cur_mtime = cur_file.stat().st_mtime
            except Exception:
                cur_mtime = 0.0

    candidates: list[tuple[float, str, str]] = []
    for f in project_dir.glob("*.jsonl"):
        try:
            stat = f.stat()
            if stat.st_size == 0:
                continue
            sid = f.stem
            mtime = stat.st_mtime

            # 读取最后有效消息摘要
            last_text = ""
            lines = f.read_text("utf-8", errors="replace").splitlines()
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                    if row.get("type") in ("user", "assistant"):
                        msg = row.get("message", {})
                        content = msg.get("content", "")
                        if isinstance(content, list):
                            parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
                            content = " ".join(p for p in parts if p)
                        if isinstance(content, str) and content.strip():
                            t = content.strip()
                            if not t.startswith("<") and t != "新会话就绪":
                                last_text = t[:50]
                                break
                except Exception:
                    continue
            candidates.append((mtime, sid, last_text))
        except Exception:
            continue

    if not candidates:
        return res

    candidates.sort(key=lambda x: -x[0])
    best_mtime, best_sid, best_summary = candidates[0]

    # 判断是否应当切换
    if not current_session_id or current_session_id == "__continue__":
        res.update({
            "has_update": True,
            "latest_session_id": best_sid,
            "latest_mtime": best_mtime,
            "summary": best_summary,
        })
    elif best_sid != current_session_id:
        if best_mtime > cur_mtime + 2.0:
            res.update({
                "has_update": True,
                "latest_session_id": best_sid,
                "latest_mtime": best_mtime,
                "summary": best_summary,
            })

    return res
