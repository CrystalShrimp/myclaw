"""工作区 / Claude 数据目录 / 原生会话读取等平台无关助手（从 feishu/events.py 搬迁）。"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path

from config.settings import settings

logger = logging.getLogger("myclaw.dispatch")


# ===== Claude native session readers =====
# claude persists the full conversation at
# ~/.claude/projects/<encoded-workspace>/<session_id>.jsonl — we read it
# back directly instead of duplicating the history in .sessions/ files.


def _find_claude_session_file(session_id: str) -> Path | None:
    """Locate the jsonl file for a claude session_id under ~/.claude/projects/.

    The intermediate directory name encodes the workspace path, but its
    exact transformation is claude-internal; we sidestep it by globbing
    on the session_id, which is unique.
    """
    if not session_id:
        return None
    projects_dir = Path.home() / ".claude" / "projects"
    if not projects_dir.exists():
        return None
    matches = list(projects_dir.glob(f"*/{session_id}.jsonl"))
    return matches[0] if matches else None


def _iter_claude_session_messages(session_id: str) -> list[dict]:
    """Return user/assistant messages from a claude session jsonl.

    Each entry: {"role": "user"|"assistant", "content": str}. Tool-only
    or system rows are skipped. Returns [] if the session file is absent
    or unreadable.
    """
    f = _find_claude_session_file(session_id)
    if f is None:
        return []
    out: list[dict] = []
    try:
        for raw in f.read_text("utf-8", errors="replace").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if row.get("type") not in ("user", "assistant"):
                continue
            msg = row.get("message", {})
            content = msg.get("content", "")
            # claude stores content as a list of blocks for assistant
            # turns; flatten to text.
            if isinstance(content, list):
                text_parts = [
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                ]
                content = "\n".join(p for p in text_parts if p)
            if not isinstance(content, str):
                content = str(content)
            out.append({"role": row["type"], "content": content})
    except Exception as e:
        logger.warning("Failed to read claude session %s: %s", session_id, e)
    return out


def _decode_output(raw: bytes) -> str:
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def restore_project_path(encoded_name: str) -> str | None:
    """根据 projects 里的编码文件夹名，还原真实的磁盘绝对路径（跨平台支持 Windows 与 macOS/Linux）。"""
    if not encoded_name or len(encoded_name) < 2:
        return None

    # 判断平台格式：
    # 1. Windows 盘符格式：例如 "D--ForRunning-ForDev-test"
    # 2. POSIX / macOS 格式：例如 "-Users-alice-projects-demo"
    if len(encoded_name) >= 4 and encoded_name[0].isalpha() and encoded_name[1:3] == "--":
        drive = encoded_name[0] + ":\\"
        root_path = Path(drive)
        remaining = encoded_name[3:]
    elif encoded_name.startswith("-"):
        root_path = Path("/")
        remaining = encoded_name.lstrip("-")
    else:
        return None

    if not root_path.exists():
        return None

    def clean(s: str) -> str:
        return re.sub(r'[^a-zA-Z0-9]', '', s).lower()

    matched = []

    def dfs(current_dir: Path, rem_str: str):
        cleaned_rem = clean(rem_str)
        if not cleaned_rem:
            matched.append(str(current_dir))
            return

        try:
            # 过滤掉一些绝对不需要遍历的巨大子目录以保证效率
            subdirs = [
                x for x in current_dir.iterdir()
                if x.is_dir() and x.name not in (".venv", "node_modules", ".git", "System", "Library")
            ]
        except Exception:
            return

        for sub in subdirs:
            cleaned_sub = clean(sub.name)
            if not cleaned_sub:
                continue
            if cleaned_rem.startswith(cleaned_sub):
                rest_str = consume_prefix(rem_str, sub.name)
                if rest_str is not None:
                    dfs(sub, rest_str)

    def consume_prefix(rem_str: str, sub_name: str) -> str | None:
        rem_idx = 0
        sub_idx = 0
        while sub_idx < len(sub_name) and rem_idx < len(rem_str):
            c_rem = rem_str[rem_idx].lower()
            c_sub = sub_name[sub_idx].lower()

            if c_rem.isalnum() and c_sub.isalnum():
                if c_rem == c_sub:
                    rem_idx += 1
                    sub_idx += 1
                else:
                    return None
            elif not c_rem.isalnum():
                rem_idx += 1
            elif not c_sub.isalnum():
                sub_idx += 1

        while sub_idx < len(sub_name):
            if sub_name[sub_idx].isalnum():
                return None
            sub_idx += 1

        while rem_idx < len(rem_str) and not rem_str[rem_idx].isalnum():
            rem_idx += 1

        return rem_str[rem_idx:]

    dfs(root_path, remaining)
    return matched[0] if matched else None


def get_project_meta(path_str: str) -> dict:
    """获取指定路径的项目关联信息 (Git 分支和 CLAUDE.md 状态)"""
    p = Path(path_str)
    meta = {
        "git_branch": "未知 (非 Git 仓库)",
        "claude_md": "不存在",
        "is_exists": p.exists()
    }

    if not p.exists():
        return meta

    if (p / "CLAUDE.md").exists():
        meta["claude_md"] = "🟢 存在"
    else:
        meta["claude_md"] = "⚪ 不存在"

    git_dir = p / ".git"
    if git_dir.exists():
        try:
            head_file = git_dir / "HEAD"
            if head_file.exists():
                head_content = head_file.read_text("utf-8").strip()
                if head_content.startswith("ref:"):
                    meta["git_branch"] = f"🌿 {head_content.split('/')[-1]}"
                else:
                    meta["git_branch"] = f"🌿 {head_content[:8]}"
        except Exception:
            pass

    return meta


def _claude_config_path() -> Path | None:
    configured = settings.claude_data_dir.strip()
    if not configured:
        home_claude = (Path.home() / ".claude").resolve()
        return home_claude if home_claude.exists() else None
    path = Path(configured).expanduser().resolve()
    if not path.exists() or not path.is_dir():
        return None
    return path


def _detected_claude_config_path() -> Path:
    return (Path.home() / ".claude").resolve()


def append_to_claude_history(workspace: str, session_id: str, display_text: str) -> None:
    """向 ~/.claude/history.jsonl 追加注册索引并修复 entrypoint 为 cli，确保交互终端 resume 能检索展示该 Session。"""
    if not session_id or session_id == "__continue__":
        return
    try:
        from app.agent.session_sync import sync_claude_session_to_cli
        sync_claude_session_to_cli(workspace, session_id, display_text)
    except Exception as e:
        logger.warning("Failed to sync claude session to cli: %s", e)


def _write_env_value(key: str, value: str) -> None:
    """Update one root .env value while preserving all unrelated settings."""
    env_path = Path(__file__).resolve().parents[2] / ".env"
    lines = env_path.read_text("utf-8").splitlines() if env_path.exists() else []
    replacement = f"{key}={value}"
    updated = False
    for index, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[index] = replacement
            updated = True
            break
    if not updated:
        lines.append(replacement)
    env_path.write_text("\n".join(lines) + "\n", "utf-8")


def find_all_claudecode_projects() -> list[str]:
    """Read every existing workspace recorded under the configured .claude dir."""
    claude_dir = _claude_config_path()
    if claude_dir is None:
        return []

    projects: dict[str, float] = {}

    def add_project(raw_path: object, active_at: object = 0) -> None:
        if not isinstance(raw_path, str) or not raw_path:
            return
        path = Path(raw_path).expanduser()
        if not path.is_absolute() or not path.is_dir():
            return
        resolved = str(path.resolve())
        try:
            timestamp = float(active_at or 0)
        except (TypeError, ValueError):
            timestamp = 0
        projects[resolved] = max(projects.get(resolved, 0), timestamp)

    history_file = claude_dir / "history.jsonl"
    if history_file.is_file():
        try:
            for line in history_file.read_text("utf-8", errors="replace").splitlines():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                add_project(row.get("project"), row.get("timestamp", 0))
        except OSError as exc:
            logger.warning("Failed to read Claude history %s: %s", history_file, exc)

    projects_dir = claude_dir / "projects"
    if projects_dir.is_dir():
        for encoded_dir in projects_dir.iterdir():
            if not encoded_dir.is_dir():
                continue
            found_cwd = False
            for session_file in encoded_dir.glob("*.jsonl"):
                try:
                    active_at = session_file.stat().st_mtime
                    with session_file.open("r", encoding="utf-8", errors="replace") as handle:
                        for line_number, line in enumerate(handle):
                            if line_number >= 100:
                                break
                            try:
                                row = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            cwd = row.get("cwd")
                            if isinstance(cwd, str) and cwd:
                                add_project(cwd, active_at)
                                found_cwd = True
                                break
                except OSError:
                    continue
            if not found_cwd:
                restored = restore_project_path(encoded_dir.name)
                add_project(restored)

    return sorted(projects, key=lambda path: (-projects[path], path.lower()))


def predict_continue_session(workspace: str) -> dict:
    """静态预判当前工作区在 `claude --continue` 时将要恢复的 Session ID 及最后一次对话摘要。"""
    if not workspace:
        return {"can_continue": False, "session_id": "", "last_summary": ""}

    ws_resolved = str(Path(workspace).resolve())
    claude_dir = _claude_config_path() or (Path.home() / ".claude")
    encoded_cwd = re.sub(r"[^A-Za-z0-9]", "-", ws_resolved)

    project_dir = claude_dir / "projects" / encoded_cwd
    if not project_dir.is_dir():
        return {"can_continue": False, "session_id": "", "last_summary": ""}

    candidates: list[tuple[float, str, str]] = []

    for f in project_dir.glob("*.jsonl"):
        try:
            mtime = f.stat().st_mtime
            sid = f.stem
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
                            last_text = content.strip()[:60]
                            break
                except Exception:
                    continue
            if last_text or len(lines) > 2:
                candidates.append((mtime, sid, last_text))
        except Exception:
            continue

    if not candidates:
        return {"can_continue": False, "session_id": "", "last_summary": ""}

    candidates.sort(key=lambda x: -x[0])
    best_mtime, best_sid, best_summary = candidates[0]
    return {
        "can_continue": True,
        "session_id": best_sid,
        "last_summary": best_summary or "包含已存在的历史对话",
    }


def list_workspace_sessions(workspace: str) -> list[dict]:
    """枚举当前工作区在 `~/.claude/projects/<encoded>/` 下的全部 Claude Session。

    返回按 mtime 倒序排列：[{"session_id", "mtime", "last_summary", "message_count"}]。
    message_count 只统计 user/assistant 文本行（与 /status 的口径一致）。
    """
    if not workspace:
        return []
    ws_resolved = str(Path(workspace).resolve())
    claude_dir = _claude_config_path() or (Path.home() / ".claude")
    encoded_cwd = re.sub(r"[^A-Za-z0-9]", "-", ws_resolved)
    project_dir = claude_dir / "projects" / encoded_cwd
    if not project_dir.is_dir():
        return []

    out: list[dict] = []
    for f in project_dir.glob("*.jsonl"):
        try:
            mtime = f.stat().st_mtime
            sid = f.stem
            last_text = ""
            msg_count = 0
            lines = f.read_text("utf-8", errors="replace").splitlines()
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("type") not in ("user", "assistant"):
                    continue
                msg_count += 1
                if not last_text:
                    msg = row.get("message", {})
                    content = msg.get("content", "")
                    if isinstance(content, list):
                        parts = [
                            b.get("text", "")
                            for b in content
                            if isinstance(b, dict) and b.get("type") == "text"
                        ]
                        content = " ".join(p for p in parts if p)
                    if isinstance(content, str) and content.strip():
                        last_text = content.strip()[:60]
            # 与 predict_continue_session 同口径：跳过几乎空白的 stub 文件
            if not (last_text or len(lines) > 2):
                continue
            out.append({
                "session_id": sid,
                "mtime": mtime,
                "last_summary": last_text or "(无文本)",
                "message_count": msg_count,
            })
        except Exception as e:
            logger.warning("Failed to read session file %s: %s", f, e)
            continue

    out.sort(key=lambda x: -x["mtime"])
    return out


def _scan_workspace_files(workspace_str: str) -> list[dict]:
    """遍历工作区下的普通文件，忽略隐藏目录与代码依赖巨型目录。"""
    ws = Path(workspace_str)
    if not ws.exists() or not ws.is_dir():
        return []

    ignore_dirs = {
        ".git", ".venv", "node_modules", "__pycache__",
        ".sessions", ".claude", ".preferences", ".pytest_cache"
    }

    result = []
    try:
        for p in ws.rglob("*"):
            if not p.is_file():
                continue
            if any(part in ignore_dirs for part in p.parts):
                continue

            try:
                stat = p.stat()
                size_bytes = stat.st_size
                mtime = stat.st_mtime
            except OSError:
                continue

            if size_bytes < 1024:
                size_str = f"{size_bytes} B"
            elif size_bytes < 1024 * 1024:
                size_str = f"{size_bytes / 1024:.1f} KB"
            else:
                size_str = f"{size_bytes / (1024 * 1024):.1f} MB"

            rel_path = str(p.relative_to(ws))
            result.append({
                "rel_path": rel_path,
                "abs_path": str(p.resolve()),
                "size_bytes": size_bytes,
                "size_str": size_str,
                "mtime": mtime,
            })
    except Exception as e:
        logger.warning("Error scanning workspace files: %s", e)

    result.sort(key=lambda x: -x["mtime"])
    return result


def log_task_failure(task: asyncio.Task) -> None:
    """Retrieve background task exceptions so asyncio does not discard them."""
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        logger.error(
            "Message dispatch failed: %s",
            error,
            exc_info=(type(error), error, error.__traceback__),
        )
