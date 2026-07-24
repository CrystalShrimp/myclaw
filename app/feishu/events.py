from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import threading
import time
import uuid
from pathlib import Path

from lark_oapi.api.im.v1.model import P2ImMessageReceiveV1
from lark_oapi.event.callback.model.p2_card_action_trigger import (
    P2CardActionTrigger,
    P2CardActionTriggerResponse,
    CallBackToast,
)

from app.agent.cli_loop import claude_cli_loop
from app.approval.manager import approval_manager
from app.feishu.client import feishu_client
from app.audit.logger import audit_logger
from app.models.schemas import Session, TaskStatus
from app.profiles import discover_profiles, test_profile
from app.state.preferences import preferences_manager
from config.settings import settings

logger = logging.getLogger("myclaw.events")


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


# ===== Session management =====


class SessionManager:
    """Session manager with file-based persistence for Claude session_id."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}  # session_id -> Session
        self._user_sessions: dict[str, str] = {}  # open_id -> session_id
        self._lock = threading.Lock()
        self._state_dir = Path(settings.audit_log_path).parent / ".sessions"
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._load_all()

    def _state_file(self, open_id: str) -> Path:
        return self._state_dir / f"{open_id}.json"

    def _load_all(self) -> None:
        """Load persisted sessions on startup."""
        if not self._state_dir.exists():
            return
        for f in self._state_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text("utf-8"))
                session = Session(**data)
                with self._lock:
                    self._sessions[session.session_id] = session
                    self._user_sessions[session.user_open_id] = session.session_id
            except Exception:
                pass

    def _save(self, session: Session) -> None:
        """Persist session to disk."""
        try:
            self._state_file(session.user_open_id).write_text(
                session.model_dump_json(indent=2), "utf-8",
            )
        except Exception as e:
            logger.warning("Failed to save session: %s", e)

    def create_session(
        self,
        user_open_id: str,
        chat_id: str,
        workspace: str | None = None,
    ) -> Session:
        session_id = uuid.uuid4().hex[:12]
        session = Session(
            session_id=session_id,
            user_open_id=user_open_id,
            chat_id=chat_id,
            workspace=workspace or settings.get_default_workspace(),
            workspace_selected=workspace is not None,
        )
        with self._lock:
            self._sessions[session_id] = session
            self._user_sessions[user_open_id] = session_id
        self._save(session)
        return session

    def get_user_session(self, open_id: str) -> Session | None:
        sid = self._user_sessions.get(open_id)
        if sid:
            return self._sessions.get(sid)
        return None

    def save_session(self, session: Session) -> None:
        """Explicitly persist session changes."""
        self._save(session)

    def clean_old_sessions(self, open_id: str) -> None:
        """Remove all session state files except the current user's."""
        if not self._state_dir.exists():
            return
        for f in self._state_dir.glob("*.json"):
            if f.stem != open_id:
                try:
                    f.unlink()
                    logger.info("Cleaned old session: %s", f.name)
                except Exception as e:
                    logger.warning("Failed to clean %s: %s", f.name, e)

    def reset_user_session(self, open_id: str) -> Session:
        old_sid = self._user_sessions.get(open_id)
        if old_sid:
            old = self._sessions.pop(old_sid, None)
            chat_id = old.chat_id if old else ""
            # 继承当前 workspace：/new 语义是"同一项目里开新会话"，不是回到 default。
            # 想换 workspace 用 /pwd。首次没有旧 session 时回落到 default。
            workspace = old.workspace if old else None
        else:
            chat_id = ""
            workspace = None
        session = self.create_session(open_id, chat_id, workspace=workspace)
        self._save(session)
        return session


session_manager = SessionManager()


# ===== Helpers =====


def _is_user_allowed(open_id: str) -> bool:
    allowed = settings.get_allowed_users()
    if not allowed:
        return True
    return open_id in allowed


def _parse_message_text(content: str) -> str:
    try:
        data = json.loads(content)
        return data.get("text", "").strip()
    except (json.JSONDecodeError, TypeError):
        return content.strip()


def _mode_label(mode: str) -> str:
    return {"h": "高容忍(全允许)", "m": "中风险(高风险审批)", "l": "低容忍(全审批)"}.get(mode, mode)





def _decode_output(raw: bytes) -> str:
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def restore_project_path(encoded_name: str) -> str | None:
    """根据 projects 里的编码文件夹名，还原真实的磁盘绝对路径。"""
    import re
    if len(encoded_name) < 4 or encoded_name[1:3] != "--":
        return None
    drive = encoded_name[0] + ":\\"
    remaining = encoded_name[3:]
    
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
                if x.is_dir() and x.name not in (".venv", "node_modules", ".git")
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
        
    dfs(Path(drive), remaining)
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
    return Path(configured).expanduser().resolve() if configured else None


def _detected_claude_config_path() -> Path:
    return (Path.home() / ".claude").resolve()


def append_to_claude_history(workspace: str, session_id: str, display_text: str) -> None:
    """向 ~/.claude/history.jsonl 追加注册索引，确保 claude --resume 交互菜单能检索展示该 Session。"""
    if not session_id or session_id == "__continue__":
        return
    try:
        home_claude = Path.home() / ".claude"
        home_claude.mkdir(parents=True, exist_ok=True)
        history_file = home_claude / "history.jsonl"

        # 如果已有相同 session_id 记录可避免重删复写，也可以直接追加
        row = {
            "display": display_text[:200] if display_text else "MyClaw Session",
            "pastedContents": {},
            "timestamp": int(time.time() * 1000),
            "project": str(Path(workspace).resolve()),
            "sessionId": session_id,
        }
        line = json.dumps(row, ensure_ascii=False) + "\n"
        with history_file.open("a", encoding="utf-8") as f:
            f.write(line)
        logger.info("Registered session %s to history.jsonl", session_id)
    except Exception as e:
        logger.warning("Failed to append to claude history.jsonl: %s", e)


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


def _claude_dir_selection_card() -> dict:
    from app.feishu.cards import build_claude_dir_selection_card
    return build_claude_dir_selection_card(str(_detected_claude_config_path()))


def _workspace_selection_card(session: Session | None = None) -> dict:
    if session and session.workspace:
        current = session.workspace
    else:
        current = settings.default_workspace.strip() or "尚未设置"
    from app.feishu.cards import build_cd_selection_card
    return build_cd_selection_card(current, find_all_claudecode_projects())


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

async def _send_card_after_callback(open_id: str, card: dict) -> None:
    """Let the WebSocket callback acknowledgement flush before sending a new card."""
    await asyncio.sleep(0.2)
    await feishu_client.send_card(open_id, card)


async def _send_text_after_callback(open_id: str, content: str) -> None:
    await asyncio.sleep(0.2)
    await feishu_client.send_text(open_id, content)

async def _send_claude_setup_result(open_id: str, resolved: str) -> None:
    await asyncio.sleep(0.2)
    await feishu_client.send_text(
        open_id, f"✅ Claude 数据目录已保存：{resolved}"
    )
    await feishu_client.send_card(open_id, _workspace_selection_card())


def _info_toast(content: str) -> P2CardActionTriggerResponse:
    """Build responses exactly as shown in the official Feishu Python sample."""
    return P2CardActionTriggerResponse({
        "toast": {"type": "info", "content": content},
    })

def _log_dispatch_failure(task: asyncio.Task) -> None:
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


async def _check_and_run_pending(open_id: str) -> bool:
    """Check if all initial setup items (Provider, Level, Mode) are complete.
    If complete and pending_prompt exists, trigger _run_claude automatically.
    """
    preferences = preferences_manager.get(open_id)
    profiles = discover_profiles()

    has_provider = preferences.model in profiles
    has_level = preferences.level in ("haiku", "sonnet", "opus")
    has_mode = preferences.mode in ("h", "m", "l")

    if has_provider and has_level and has_mode:
        session = session_manager.get_user_session(open_id)
        if session and session.pending_prompt.strip():
            pending = session.pending_prompt.strip()
            session.pending_prompt = ""
            session_manager.save_session(session)

            profile_label = profiles.get(preferences.model, {}).get("label", preferences.model)
            mode_labels = {"h": "⚡ 全自动模式 (h)", "m": "⚖️ 平衡模式 (m)", "l": "🛡️ 严格模式 (l)"}
            mode_lbl = mode_labels.get(preferences.mode, preferences.mode)

            prompt_preview = pending if len(pending) <= 30 else pending[:27] + "..."
            msg_lines = [
                "🎉 初始配置已全部就绪！",
                "",
                "📋 当前运行设置：",
                f"• 供应商 (Provider)：`{profile_label}`",
                f"• 规格 (Model Level)：`{preferences.level}`",
                f"• 审批模式 (Mode)：`{mode_lbl}`",
                "",
                f"正在全自动为您执行暂存的任务：`{prompt_preview}` ..."
            ]
            await feishu_client.send_text(open_id, "\n".join(msg_lines))
            asyncio.get_running_loop().create_task(_run_claude(pending, open_id, session))
            return True
    return False

# ===== Event handlers =====


def on_message_receive(event: P2ImMessageReceiveV1) -> None:
    if not event.event:
        logger.debug("on_message_receive: no event payload")
        return
    msg = event.event.message
    sender = event.event.sender
    if not msg or not sender or not sender.sender_id:
        logger.debug("on_message_receive: missing msg/sender")
        return
    if msg.message_type != "text":
        return
    open_id = sender.sender_id.open_id or ""
    chat_id = msg.chat_id or ""
    message_id = msg.message_id or ""
    text = _parse_message_text(msg.content or "{}")
    if not text:
        return
    logger.info("From %s: %s", open_id, text[:100])
    task = asyncio.get_running_loop().create_task(
        _dispatch(open_id, chat_id, message_id, text)
    )
    task.add_done_callback(_log_dispatch_failure)


def on_card_action(event: P2CardActionTrigger) -> P2CardActionTriggerResponse:
    if not event.event:
        return P2CardActionTriggerResponse()
    action = event.event.action
    operator = event.event.operator
    if not action or not action.value or not operator:
        return P2CardActionTriggerResponse()
        
    card_type = action.value.get("type", "")
    act = action.value.get("act", "")
    open_id = operator.open_id or ""
    logger.info(
        "Card action: type=%s act=%s option=%r form_keys=%s",
        card_type,
        act,
        action.option,
        sorted((action.form_value or {}).keys()),
    )
    
    # First-run setup: locate the Claude data directory before listing workspaces.
    if card_type == "claude_dir_select":
        resp = P2CardActionTriggerResponse()
        toast = CallBackToast()
        act = action.value.get("act", "")
        if act == "set_detected":
            target_path = action.value.get("path", "")
        else:
            target_path = ""
            if action.form_value:
                target_path = action.form_value.get("claude_data_dir") or ""
        path = Path(target_path.strip()).expanduser()
        if not path.is_absolute() or not path.is_dir():
            toast.type = "error"
            toast.content = "请输入存在的 .claude 文件夹绝对路径"
            resp.toast = toast
            return resp
        if not (path / "history.jsonl").is_file() and not (path / "projects").is_dir():
            toast.type = "error"
            toast.content = "该目录中未找到 history.jsonl 或 projects 文件夹"
            resp.toast = toast
            return resp
        resolved = str(path.resolve())
        _write_env_value("CLAUDE_DATA_DIR", resolved)
        settings.claude_data_dir = resolved
        task = asyncio.get_running_loop().create_task(
            _send_claude_setup_result(open_id, resolved)
        )
        task.add_done_callback(_log_dispatch_failure)
        return P2CardActionTriggerResponse({})
    # 拦截并处理工作区选择、确认与返回
    if card_type == "workspace_select":
        act = action.value.get("act", "")
        resp = P2CardActionTriggerResponse()
        toast = CallBackToast()
        
        # 1. 第一阶段预处理：拉起确认卡片
        if act in ("pre_switch", "pre_create"):
            target_path = ""
            if act == "pre_switch":
                target_path = action.option or ""
            elif act == "pre_create":
                if action.form_value:
                    target_path = action.form_value.get("workspace_path") or ""
                if not target_path:
                    target_path = action.option or ""
                    
            target_path = target_path.strip()
            if not target_path:
                toast.type = "error"
                toast.content = "未检测到路径。您可以直接发送指令：/cd <绝对路径>"
                resp.toast = toast
                return resp
                
            p = Path(target_path)
            if not p.is_absolute():
                toast.type = "error"
                toast.content = "错误：请输入绝对路径"
                resp.toast = toast
                return resp
                
            is_new = False
            if not p.exists():
                if act == "pre_create":
                    # 校验父目录是否存在
                    parent = p.parent
                    if not parent.exists():
                        toast.type = "error"
                        toast.content = f"校验失败：父目录 `{parent}` 不存在！"
                        resp.toast = toast
                        return resp
                    is_new = True
                else:
                    toast.type = "error"
                    toast.content = f"错误：路径不存在: `{target_path}`"
                    resp.toast = toast
                    return resp
                    
            # 探测元数据
            meta = get_project_meta(target_path)
            # 检查是否有任务正在运行
            warning_running = claude_cli_loop.is_running(open_id)
            
            from app.feishu.cards import build_cd_confirm_card
            confirm_card = build_cd_confirm_card(
                target_path=str(p.resolve()),
                is_new=is_new,
                git_branch=meta["git_branch"],
                claude_md=meta["claude_md"],
                warning_running=warning_running
            )
            task = asyncio.get_running_loop().create_task(
                _send_card_after_callback(open_id, confirm_card)
            )
            task.add_done_callback(_log_dispatch_failure)
            return P2CardActionTriggerResponse({})
            
        # 2. 第二阶段真正动作：确认切换
        elif act == "confirm_switch":
            target_path = action.value.get("path", "").strip()
            if not target_path:
                toast.type = "error"
                toast.content = "路径信息丢失，请重新选择"
                resp.toast = toast
                return resp
                
            p = Path(target_path)
            is_new = False
            if not p.exists():
                parent = p.parent
                if not parent.exists():
                    toast.type = "error"
                    toast.content = f"创建失败：父目录 `{parent}` 不存在！"
                    resp.toast = toast
                    return resp
                try:
                    p.mkdir(parents=False, exist_ok=True)
                    is_new = True
                except Exception as e:
                    toast.type = "error"
                    toast.content = f"无法创建目录: {e}"
                    resp.toast = toast
                    return resp
                    
            session = session_manager.get_user_session(open_id)
            if not session:
                session = session_manager.create_session(open_id, "", workspace=str(p.resolve()))
            else:
                session.workspace = str(p.resolve())
                session.workspace_selected = True
                session.claude_session_id = "__continue__"  # 切换工作区后全自动带 --continue 恢复该项目最新 Session 历史
                session_manager.save_session(session)

            preferences_manager.clear(open_id)
            resolved_workspace = str(p.resolve())
            _write_env_value("DEFAULT_WORKSPACE", resolved_workspace)
            settings.default_workspace = resolved_workspace
            claude_cli_loop.cancel_by_user(open_id)
            
            action_msg = "已在新目录新建并切换" if is_new else "已切换"
            task = asyncio.get_running_loop().create_task(
                _send_text_after_callback(open_id, f"📁 {action_msg}工作区：{session.workspace}")
            )
            task.add_done_callback(_log_dispatch_failure)
            return P2CardActionTriggerResponse({})
            
        # 3. 第二阶段动作：取消并返回第一阶段卡片
        elif act == "cancel_switch":
            task = asyncio.get_running_loop().create_task(
                _send_card_after_callback(open_id, _workspace_selection_card())
            )
            task.add_done_callback(_log_dispatch_failure)
            return P2CardActionTriggerResponse({})

    # 拦截并处理文件发送卡片动作
    if card_type == "file_send_select" and act == "send_file":
        target_path = action.option or ""
        if not target_path and action.value:
            target_path = action.value.get("path", "")

        if not target_path:
            return P2CardActionTriggerResponse({
                "toast": {"type": "error", "content": "错误：未选择任何文件"}
            })

        p = Path(target_path)
        if not p.exists() or not p.is_file():
            return P2CardActionTriggerResponse({
                "toast": {"type": "error", "content": f"文件不存在：{p.name}"}
            })

        if p.stat().st_size > 30 * 1024 * 1024:
            return P2CardActionTriggerResponse({
                "toast": {"type": "error", "content": f"文件过大 ({p.stat().st_size / (1024*1024):.1f}MB)，飞书上限 30MB"}
            })

        async def _upload_and_send_task() -> None:
            try:
                await feishu_client.send_text(open_id, f"⏳ 正在上传并发送文件：`{p.name}` ...")
                file_key = await feishu_client.upload_file(p)
                await feishu_client.send_file(open_id, file_key)
            except Exception as e:
                logger.exception("Failed to send file to user %s: %s", open_id, e)
                await feishu_client.send_text(open_id, f"❌ 发送文件失败：{e}")

        task = asyncio.get_running_loop().create_task(_upload_and_send_task())
        task.add_done_callback(_log_dispatch_failure)

        return P2CardActionTriggerResponse({
            "toast": {"type": "info", "content": f"已开始发送文件：{p.name}"}
        })

    approval_id = action.value.get("approval_id", "")
    act = action.value.get("act", "")
    # Mode selection card handler
    if card_type == "mode_switch" and act == "switch_mode":
        target_mode = action.value.get("mode", "")
        if target_mode not in ("h", "m", "l"):
            target_mode = "m"
        preferences = preferences_manager.get(open_id)
        preferences.mode = target_mode
        preferences_manager.save(open_id, preferences)

        triggered = asyncio.get_running_loop().create_task(_check_and_run_pending(open_id))

        resp = P2CardActionTriggerResponse()
        toast = CallBackToast()
        mode_labels = {"h": "严格模式 (h)", "m": "平衡模式 (m)", "l": "全自动模式 (l)"}
        lbl = mode_labels.get(target_mode, target_mode)
        toast.type = "info"
        toast.content = f"已设置审批模式: {lbl}"
        resp.toast = toast
        return resp

    # Model selection card: switch_model → approve + update session model
    if card_type == "model_selection" and act == "switch_model":
        target_model = action.value.get("model", "")
        if target_model not in ("haiku", "sonnet", "opus"):
            target_model = "sonnet"
        preferences = preferences_manager.get(open_id)
        preferences.level = target_model
        preferences_manager.save(open_id, preferences)
        approval_manager.set_switch_model(approval_id, target_model)
        asyncio.get_running_loop().create_task(
            approval_manager.handle_decision(approval_id, open_id, True)
        )
        asyncio.get_running_loop().create_task(_check_and_run_pending(open_id))

        resp = P2CardActionTriggerResponse()
        toast = CallBackToast()
        toast.type = "info"
        toast.content = f"已切换模型规格: {target_model}"
        resp.toast = toast
        return resp

    # Provider model selection card.
    if card_type == "profile_switch" and act == "switch_profile":
        profile_name = action.value.get("profile", "")
        profiles = discover_profiles()
        label = profiles.get(profile_name, {}).get("label", profile_name)
        resp = P2CardActionTriggerResponse()
        toast = CallBackToast()
        if profile_name not in profiles:
            toast.type = "error"
            toast.content = f"切换失败: 未找到 {label} 配置"
            resp.toast = toast
            return resp

        preferences = preferences_manager.get(open_id)
        preferences.model = profile_name
        preferences_manager.save(open_id, preferences)
        claude_cli_loop.cancel_by_user(open_id)
        ok, detail = test_profile(profile_name)

        if ok:
            asyncio.get_running_loop().create_task(_check_and_run_pending(open_id))

        toast.type = "success" if ok else "error"
        toast.content = (
            f"模型已切换为 {label}"
            if ok else f"模型不可用: {label} ({detail})"
        )
        resp.toast = toast
        return resp
    approved = act == "approve"
    card_label = "工具执行" if card_type == "tool_execution" else "审批"
    asyncio.get_running_loop().create_task(
        approval_manager.handle_decision(approval_id, open_id, approved)
    )

    # Update card to show result (replaces buttons with status text)
    resp = P2CardActionTriggerResponse()
    toast = CallBackToast()
    toast.type = "success" if approved else "error"
    toast.content = "已允许" if approved else "已拒绝"
    resp.toast = toast
    resp.card = _build_done_card(
        title=f"{'✅' if approved else '❌'} {card_label}已{'允许' if approved else '拒绝'}",
        color="green" if approved else "red",
        approval_id=approval_id,
    )
    return resp


def _build_done_card(title: str, color: str, approval_id: str) -> object:
    """Build a minimal card that replaces the approval card after decision."""
    from lark_oapi.event.callback.model.p2_card_action_trigger import CallBackCard
    card = CallBackCard()
    card.type = "raw"
    card.data = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": color,
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": f"审批ID: `{approval_id}`"}},
        ],
    }
    return card


# ===== Core dispatch =====


async def _dispatch(open_id: str, chat_id: str, message_id: str, text: str) -> None:
    if not _is_user_allowed(open_id):
        await feishu_client.send_text(open_id, "抱歉，您没有使用权限。")
        return

    text = text.strip()
    text_lower = text.lower()

    session = session_manager.get_user_session(open_id)
    if not settings.claude_data_dir.strip():
        await feishu_client.send_card(open_id, _claude_dir_selection_card())
        return

    # --- /stop: interrupt current task ---
    if text_lower in ("/stop", "停止"):
        cancelled = await claude_cli_loop.cancel_and_wait(open_id)
        if cancelled:
            await feishu_client.send_text(open_id, "已中断会话。")
        else:
            await feishu_client.send_text(open_id, "没有正在运行的任务。")
        return

    # --- /reset: reset all setup preferences for testing ---
    if text_lower in ("/reset", "重置"):
        preferences = preferences_manager.get(open_id)
        preferences.model = ""
        preferences.level = ""
        preferences.mode = ""
        preferences_manager.save(open_id, preferences)
        await feishu_client.send_text(
            open_id,
            "已清空所有初始设置（Provider / Model Level / Mode）。\n"
            "您现在可以发送一条普通任务，测试全套卡片一次性连续弹出与全就绪自动重发的全流程。",
        )
        return

    # --- /status ---
    if text_lower == "/status":
        try:
            session = session_manager.get_user_session(open_id)
            preferences = preferences_manager.get(open_id)
            if session:
                # Build context usage info
                ctx_info = ""
                if session.context_tokens > 0:
                    pct = session.context_tokens / session.context_limit * 100
                    ctx_info = (
                        f"上下文用量: `{session.context_tokens:,}` / `{session.context_limit:,}` ({pct:.0f}%)\n"
                    )
                    if pct >= settings.context_critical_percent:
                        if settings.compact_enabled:
                            ctx_info += "⚠️ 上下文即将耗尽，建议使用 `/compact` 压缩或 `/new` 开始新会话\n"
                        else:
                            ctx_info += "⚠️ 上下文即将耗尽，建议使用 `/new` 开始新会话\n"
                    elif pct >= settings.context_warn_percent:
                        if settings.compact_enabled:
                            ctx_info += "⚠️ 上下文用量较高，可以用 `/compact` 压缩上下文\n"
                        else:
                            ctx_info += "⚠️ 上下文用量较高，请注意\n"
                
                csid = session.claude_session_id or "未启动"
                msg_count = 0
                if session.claude_session_id and session.claude_session_id != "__continue__":
                    msg_count = len(_iter_claude_session_messages(session.claude_session_id))
                elif session.claude_session_id == "__continue__":
                    csid = "全自动恢复该项目最新 Session (--continue)"

                await feishu_client.send_text(
                    open_id,
                    f"📁 工作区: `{session.workspace}`\n"
                    f"🔑 Claude Session: `{csid}`\n"
                    f"🤖 模型供应商: `{preferences.model or '未选择'}`\n"
                    f"⚡ 模型规格: `{preferences.level or '未选择'}`\n"
                    f"🛡️ 审批模式: `{preferences.mode or '未选择'}`\n"
                    f"💬 底层推演步数: {msg_count} 步 (含思考/工具调用记录)\n"
                    f"{ctx_info}"
                    f"状态: {session.status.value}",
                )
            else:
                await feishu_client.send_text(open_id, "没有活跃会话。用 /new 创建新会话。")
        except Exception as err:
            logger.exception("Error handling /status for user %s", open_id)
            await feishu_client.send_text(open_id, f"状态获取失败：{err}")
        return

    # --- /help 或 帮助 ---
    if text_lower in ("/help", "帮助", "help"):
        from app.feishu.cards import build_help_card
        await feishu_client.send_card(open_id, build_help_card())
        return

    # --- /provider [profile]: choose provider profile ---
    if text_lower == "/provider" or text_lower.startswith("/provider "):
        parts = text.split(None, 1)
        profiles = discover_profiles()
        preferences = preferences_manager.get(open_id)
        if not profiles:
            await feishu_client.send_text(open_id, "未发现 config/settings_*.json 模型配置。")
            return
        if len(parts) == 2:
            name = parts[1].strip().lower()
            if name in ("reset", "clear"):
                preferences.model = ""
                preferences.level = ""
                preferences.mode = ""
                preferences_manager.save(open_id, preferences)
                await feishu_client.send_text(
                    open_id,
                    "已清空所有初始设置（Provider / Model Level / Mode）。\n您现在可以发送一条普通任务，测试全套卡片一次性连续弹出与全就绪自动重发的全流程。",
                )
                return
            if name not in profiles:
                await feishu_client.send_text(
                    open_id, f"未知供应商: `{name}`\n可用: {'、'.join(profiles.keys())}",
                )
                return
            preferences.model = name
            preferences_manager.save(open_id, preferences)
            await claude_cli_loop.cancel_and_wait(open_id)
            ok, detail = test_profile(name)
            label = profiles[name].get("label", name)
            await feishu_client.send_text(
                open_id,
                f"模型供应商已切换为 `{label}`。" if ok else f"模型供应商 `{label}` 不可用: {detail}",
            )
            return

        from app.feishu.cards import build_profile_selection_card
        card = build_profile_selection_card(
            approval_id=uuid.uuid4().hex[:12],
            profiles=profiles,
            active_profile=preferences.model,
        )
        await feishu_client.send_card(open_id, card)
        return

    # --- /model [haiku|sonnet|opus] / /level: choose model capability level ---
    if (
        text_lower == "/model" or text_lower.startswith("/model ")
        or text_lower == "/level" or text_lower.startswith("/level ")
    ):
        parts = text.split(None, 1)
        preferences = preferences_manager.get(open_id)
        profiles = discover_profiles()
        valid_levels = ("haiku", "sonnet", "opus")

        if len(parts) == 2:
            arg = parts[1].strip().lower()
            if arg in valid_levels:
                preferences.level = arg
                preferences_manager.save(open_id, preferences)
                await claude_cli_loop.cancel_and_wait(open_id)
                await feishu_client.send_text(open_id, f"模型规格已切换为 `{preferences.level}`。")
                return
            elif arg in profiles:
                await feishu_client.send_text(
                    open_id,
                    f"⚠️ `{arg}` 是模型供应商 (Provider)。\n"
                    f"切换供应商请使用：`/provider {arg}`\n"
                    f"切换模型规格请使用：`/model haiku|sonnet|opus`",
                )
                return

        await feishu_client.send_text(
            open_id,
            f"当前模型规格: `{preferences.level or '未选择'}`\n\n"
            "用法: `/model haiku|sonnet|opus` (也可使用 `/level`)",
        )
        return

    # --- /mode [h|m|l]: choose approval mode ---
    if text_lower == "/mode" or text_lower.startswith("/mode "):
        parts = text.split(None, 1)
        preferences = preferences_manager.get(open_id)
        if len(parts) == 2 and parts[1].strip().lower() in ("h", "m", "l"):
            preferences.mode = parts[1].strip().lower()
            preferences_manager.save(open_id, preferences)
            await feishu_client.send_text(open_id, f"审批模式已切换为 `{preferences.mode}`。")
            await _check_and_run_pending(open_id)
            return

        from app.feishu.cards import build_mode_selection_card
        card = build_mode_selection_card(
            approval_id=uuid.uuid4().hex[:12],
            active_mode=preferences.mode,
        )
        await feishu_client.send_card(open_id, card)
        return
    # --- /pwd: print current workspace path ---
    if text_lower == "/pwd" or text_lower.startswith("/pwd "):
        session = session_manager.get_user_session(open_id)
        ws = session.workspace if session else settings.get_default_workspace()
        await feishu_client.send_text(open_id, f"📁 当前工作目录：`{ws}`")
        return

    # --- /show: select and send workspace files ---
    if text_lower == "/show" or text_lower.startswith("/show "):
        session = session_manager.get_user_session(open_id)
        ws = session.workspace if session else settings.get_default_workspace()
        files = _scan_workspace_files(ws)
        from app.feishu.cards import build_file_selection_card
        card = build_file_selection_card(ws, files)
        await feishu_client.send_card(open_id, card)
        return

    # --- /cd <path>: switch workspace ---
    if text_lower.startswith("/cd"):
        parts = text.split(None, 1)
        
        # 1. 如果不带参数，展示卡片选择已有项目或新建
        if len(parts) < 2 or not parts[1].strip():
            session = session_manager.get_user_session(open_id)
            card = _workspace_selection_card(session)
            await feishu_client.send_card(open_id, card)
            return
            
        # 2. 如果带路径参数，直接校验切换
        new_path = parts[1].strip()
        p = Path(new_path)
        if not p.is_absolute():
            await feishu_client.send_text(
                open_id, "错误：请使用绝对路径，例如 `/cd D:\\projects\\myapp`",
            )
            return
            
        if not p.exists():
            # 检查父目录是否存在
            parent = p.parent
            if not parent.exists():
                await feishu_client.send_text(
                    open_id, f"❌ 切换失败：父目录 `{parent}` 在磁盘上不存在！"
                )
                return
            
            # 目录不存在但父目录存在 — 弹出前置确认卡片，待用户确认后再建目录切换
            target_path = str(p.resolve())
            meta = get_project_meta(target_path)
            warning_running = claude_cli_loop.is_running(open_id)
            
            from app.feishu.cards import build_cd_confirm_card
            confirm_card = build_cd_confirm_card(
                target_path=target_path,
                is_new=True,
                git_branch=meta["git_branch"],
                claude_md=meta["claude_md"],
                warning_running=warning_running
            )
            await feishu_client.send_card(open_id, confirm_card)
            return
                
        session = session_manager.get_user_session(open_id)
        if not session:
            session = session_manager.create_session(open_id, chat_id, workspace=str(p.resolve()))
        else:
            session.workspace = str(p.resolve())
            session.workspace_selected = True
            session.claude_session_id = "__continue__"  # 切换工作区后全自动带 --continue 恢复该项目最新 Session 历史
            session_manager.save_session(session)

        preferences_manager.clear(open_id)
        resolved_workspace = str(p.resolve())
        _write_env_value("DEFAULT_WORKSPACE", resolved_workspace)
        settings.default_workspace = resolved_workspace
        claude_cli_loop.cancel_by_user(open_id)
        
        action_msg = "已在新目录新建并切换" if is_new else "工作区已切换"
        await feishu_client.send_text(open_id, f"📁 {action_msg}：`{session.workspace}`")
        return

    # --- /resume <session_id>: switch to one exact native session ---
    if text_lower.startswith("/resume"):
        parts = text.split(None, 1)
        session = session_manager.get_user_session(open_id)
        if len(parts) < 2 or not parts[1].strip():
            cur = session.claude_session_id if session else ""
            await feishu_client.send_text(
                open_id,
                f"当前 Claude Session: `{cur or '无'}`\n\n用法: `/resume <session_id>`",
            )
            return
        if not session:
            session = session_manager.create_session(open_id, chat_id)
        await claude_cli_loop.cancel_and_wait(open_id)
        session.claude_session_id = parts[1].strip()
        session.context_tokens = 0
        session_manager.save_session(session)
        preferences_manager.clear(open_id)
        await feishu_client.send_text(
            open_id,
            f"已切换到 Claude Session `{session.claude_session_id}`。\n"
            "运行参数已清空，请依次设置 `/model`、`/level`、`/mode`。",
        )
        return
    # --- /continue [prompt]: resume most recent session via native --continue ---
    if text_lower.startswith("/continue"):
        parts = text.split(None, 1)
        session = session_manager.get_user_session(open_id)
        if not session:
            session = session_manager.create_session(open_id, chat_id)

        arg = parts[1].strip() if len(parts) > 1 else ""
        # /continue <session_id> 形式不再有意义（claude 总是恢复最近会话），
        # 但仍接受参数当作普通提示处理。
        prompt = arg if arg else "继续上次的任务"

        # Force --continue even if myclaw has no recorded session: claude
        # looks up the most recent session in the workspace itself. Sentinel
        # just makes _start_process append --continue.
        if not session.claude_session_id:
            session.claude_session_id = "__continue__"
            session_manager.save_session(session)
            preferences_manager.clear(open_id)
        await _run_claude(prompt, open_id, session, skip_classify=True)
        return

    # --- /new: reset native session and runtime preferences ---
    if text_lower == "/new":
        await claude_cli_loop.cancel_and_wait(open_id)
        session = session_manager.reset_user_session(open_id)
        preferences_manager.clear(open_id)
        await feishu_client.send_text(
            open_id,
            f"✨ 新会话已重置。\n"
            f"📁 工作区: `{session.workspace}`\n"
            f"🔑 Claude Session: `待生成 (发送第一条消息后自动分配)`\n"
            "运行参数已清空，请依次设置 `/model`、`/level`、`/mode`。",
        )
        return
    # --- /clean: remove old session files, keep only current ---
    if text_lower == "/clean":
        session = session_manager.get_user_session(open_id)
        if session:
            session_manager.clean_old_sessions(open_id)
            await feishu_client.send_text(
                open_id,
                f"已清理旧会话文件，当前会话: `{session.session_id}`",
            )
        else:
            await feishu_client.send_text(open_id, "没有活跃会话。")
        return

    # --- /compact: trigger CLI's built-in compact on current session ---
    if text_lower == "/compact":
        if not settings.compact_enabled:
            await feishu_client.send_text(open_id, "压缩功能已禁用。请联系管理员开启。")
            return
        session = session_manager.get_user_session(open_id)
        if not session or not session.claude_session_id:
            await feishu_client.send_text(
                open_id,
                "没有活跃的 Claude 会话，无法压缩。\n"
                "先发一条消息启动会话，上下文不足时再使用 `/compact`。",
            )
            return
        await _run_claude("/compact", open_id, session, skip_classify=True)
        return

    # --- /mem <text>: append memo to CLAUDE.md in current workspace ---
    # CLAUDE.md is the filename claude auto-loads as in-session memory,
    # so writing there means the next prompt sees the memo with no extra
    # wiring. Legacy AGENT.md is migrated on first write.
    if text_lower.startswith("/mem "):
        content = text[5:].strip()
        if not content:
            await feishu_client.send_text(
                open_id, "用法: `/mem <内容>`，例如 `/mem CSMAR弹窗需要先关闭才能操作`",
            )
            return
        session = session_manager.get_user_session(open_id)
        workspace = session.workspace if session else settings.get_default_workspace()
        memory_md = Path(workspace) / "CLAUDE.md"
        legacy_md = Path(workspace) / "AGENT.md"
        try:
            # One-shot migration: fold legacy AGENT.md into CLAUDE.md so
            # claude actually picks it up.
            if legacy_md.exists() and not memory_md.exists():
                memory_md.write_text(legacy_md.read_text("utf-8"), "utf-8")
                legacy_md.unlink()
                logger.info("Migrated %s → %s", legacy_md, memory_md)

            if memory_md.exists():
                existing = memory_md.read_text("utf-8").rstrip("\n")
                memory_md.write_text(existing + "\n" + content + "\n", "utf-8")
            else:
                memory_md.write_text(content + "\n", "utf-8")
            await feishu_client.send_text(open_id, f"已记录到 `{workspace}` 下的 CLAUDE.md")
        except Exception as e:
            await feishu_client.send_text(open_id, f"写入失败: {e}")
        return

    # --- /sh <command>: execute shell command in workspace ---
    if text_lower.startswith("/sh "):
        cmd = text[4:].strip()
        if not cmd:
            await feishu_client.send_text(
                open_id, "用法: `/sh <command>`，例如 `/sh mkdir ZhiWang`",
            )
            return
        session = session_manager.get_user_session(open_id)
        workspace = session.workspace if session else settings.get_default_workspace()
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workspace,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            out = _decode_output(stdout).strip()
            err = _decode_output(stderr).strip()
        except asyncio.TimeoutError:
            proc.kill()
            await feishu_client.send_text(open_id, f"⏱️ 命令超时(30s): `{cmd}`")
            return
        except Exception as e:
            await feishu_client.send_text(open_id, f"执行失败: `{e}`")
            return
        parts = [f"📁 `{workspace}`", f"▶ `{cmd}`"]
        if out:
            display = out if len(out) <= 4000 else out[:3950] + "\n... (输出已截断)"
            parts.append(f"```\n{display}\n```")
        if err:
            display = err if len(err) <= 1000 else err[:950] + "\n..."
            parts.append(f"⚠️ stderr:\n```\n{display}\n```")
        parts.append(f"退出码: {proc.returncode}")
        await feishu_client.send_text(open_id, "\n".join(parts))
        return

    # --- Default: run Claude CLI ---
    await _run_claude(text, open_id)


async def _run_claude(
    prompt: str,
    open_id: str,
    session: Session | None = None,
    skip_classify: bool = False,
    resume_session_id: str | None = None,
) -> None:
    """Run Claude CLI with optional model selection card for first message.

    resume_session_id: if set, start claude with ``--resume <id>`` to
    continue a specific session (used by /resume <id>). When None, the
    claude_session_id on the session drives ``--continue``.
    """
    audit_logger.log_command_received(open_id, prompt, "started")
    try:
        if not session:
            session = session_manager.get_user_session(open_id)
        if not session:
            session = session_manager.create_session(open_id, "")

        preferences = preferences_manager.get(open_id)
        profiles = discover_profiles()

        need_provider = preferences.model not in profiles
        need_level = preferences.level not in ("haiku", "sonnet", "opus")
        need_mode = preferences.mode not in ("h", "m", "l")

        if need_provider or need_level or need_mode:
            session.pending_prompt = prompt
            session_manager.save_session(session)

            missing_cards = []
            if need_provider:
                from app.feishu.cards import build_profile_selection_card
                missing_cards.append(
                    build_profile_selection_card(
                        approval_id=uuid.uuid4().hex[:12],
                        profiles=profiles,
                        active_profile="",
                    )
                )
            if need_level:
                from app.feishu.cards import build_model_selection_card
                missing_cards.append(
                    build_model_selection_card(
                        approval_id=uuid.uuid4().hex[:12],
                        current_model="",
                    )
                )
            if need_mode:
                from app.feishu.cards import build_mode_selection_card
                missing_cards.append(
                    build_mode_selection_card(
                        approval_id=uuid.uuid4().hex[:12],
                        active_mode="",
                    )
                )

            for card in missing_cards:
                await feishu_client.send_card(open_id, card)

            prompt_preview = prompt if len(prompt) <= 30 else prompt[:27] + "..."
            await feishu_client.send_text(
                open_id,
                f"💡 任务已安全暂存：`{prompt_preview}`\n"
                f"系统检测到有 {len(missing_cards)} 项初始配置尚未设置。请直接在上方卡片中点选完成，全部设置就绪后系统将全自动重新开始为您执行任务！",
            )
            return

        agent_result = await claude_cli_loop.send_and_wait(
            prompt=prompt,
            open_id=open_id,
            workspace=session.workspace,
            model=preferences.level,
            approval_mode=preferences.mode,
            profile_name=preferences.model,
            claude_session_id=session.claude_session_id or None,
            resume_session_id=resume_session_id,
        )
        # Process was killed (switch/stop/new) — caller already notified user
        if agent_result.status == "cancelled":
            return

        # Preserve the last known-good ID on transient execution failures.
        # /new and workspace changes are the explicit reset operations.
        if agent_result.session_id:
            session.claude_session_id = agent_result.session_id
            append_to_claude_history(session.workspace, agent_result.session_id, prompt)
        if agent_result.input_tokens > 0:
            session.context_tokens = agent_result.input_tokens
        # NOTE: agent_messages intentionally not appended — claude already
        # persists the full conversation in ~/.claude/projects/*/<sid>.jsonl,
        # which _iter_claude_session_messages reads back on demand.

        # Build context warning suffix for notifications
        ctx_warning = ""
        if session.context_tokens > 0:
            ctx_pct = session.context_tokens / session.context_limit * 100
            if ctx_pct >= settings.context_critical_percent:
                if settings.compact_enabled:
                    ctx_warning = (
                        f"\n\n🚨 **上下文已用 {ctx_pct:.0f}%，即将耗尽！**\n"
                        f"使用 `/compact` 压缩上下文继续对话，或 `/new` 开始全新会话。"
                    )
                else:
                    ctx_warning = (
                        f"\n\n🚨 **上下文已用 {ctx_pct:.0f}%，即将耗尽！**\n"
                        f"请使用 `/new` 开始新会话，否则后续对话可能报错。"
                    )
            elif ctx_pct >= settings.context_warn_percent:
                if settings.compact_enabled:
                    ctx_warning = (
                        f"\n\n⚠️ 上下文用量: {ctx_pct:.0f}%，可以用 `/compact` 压缩上下文。"
                    )
                else:
                    ctx_warning = (
                        f"\n\n⚠️ 上下文用量: {ctx_pct:.0f}%，建议适时用 `/new` 开新会话。"
                    )

        # Persist session to disk
        session_manager.save_session(session)

        try:
            session.status = TaskStatus(agent_result.status)
        except ValueError:
            session.status = TaskStatus.COMPLETED

        # Send result summary notification
        if agent_result.status == "completed":
            sid_hint = ""
            if session.claude_session_id and session.claude_session_id != "__continue__":
                sid_hint = f"\nClaude Session: `{session.claude_session_id}`"
            ctx_info = ""
            if session.context_tokens > 0:
                ctx_info = f" | 上下文: {session.context_tokens:,}"
            await feishu_client.send_text(
                open_id,
                f"✅ 任务完成 | 耗时: {agent_result.duration_s:.1f}s | "
                f"工具: {len(agent_result.tools_used)}次 | "
                f"费用: ${agent_result.cost_usd:.4f}{ctx_info}"
                f"{sid_hint}\n\n"
                f"直接发消息即可继续对话，或用 `/continue` 恢复上次会话。"
                f"{ctx_warning}",
            )
        elif agent_result.status == "failed":
            await feishu_client.send_text(
                open_id,
                f"❌ 任务失败 | 原因: {agent_result.error[:200]}\n\n"
                f"发消息即可重试，或用 `/continue` 恢复会话。"
                f"{ctx_warning}",
            )
        elif agent_result.status == "cancelled":
            await feishu_client.send_text(open_id, "任务已取消。发消息即可开始新任务。")

        audit_logger.log_command_received(open_id, prompt, agent_result.status)

    except Exception as e:
        logger.exception("Dispatch error for user %s", open_id)
        try:
            await feishu_client.send_text(
                open_id,
                f"处理指令时出错：{type(e).__name__}: {e}",
            )
        except Exception:
            pass
