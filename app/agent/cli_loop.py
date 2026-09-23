"""Claude Code CLI subprocess manager — interactive mode.

Spawns `claude` with --print --input-format stream-json and keeps
the process alive.  User messages are written to stdin as JSONL
and responses are read from stdout by a background reader task.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path


def _summarize_tool_args(name: str, args: dict) -> str:
    """One-line summary of what a tool is doing, for the progress card."""
    if not isinstance(args, dict):
        return ""
    if name == "Bash":
        cmd = (args.get("command") or "").strip()
        if not cmd:
            return ""
        first = cmd.split("&&")[0].split("|")[0].split(";")[0].strip()
        return f"`{first[:80]}`" if len(first) <= 80 else f"`{first[:77]}…`"
    if name in ("Read", "Write", "Edit", "NotebookEdit"):
        path = args.get("file_path") or ""
        if not path:
            return ""
        leaf = path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
        return f"`{leaf}`"
    if name == "Grep":
        pat = (args.get("pattern") or "").strip()
        return f"`{pat[:40]}`" if pat else ""
    if name == "Glob":
        pat = (args.get("pattern") or "").strip()
        return f"`{pat[:40]}`" if pat else ""
    if name in ("TaskCreate", "TaskUpdate"):
        subj = (args.get("subject") or "").strip()
        return f"`{subj[:40]}`" if subj else ""
    return ""


def _extract_warning_summary(attachment: dict) -> str:
    """Short human-readable summary from an attachment event (hook error etc.)."""
    name = attachment.get("hookName") or attachment.get("type") or "attachment"
    stderr = (attachment.get("stderr") or "").strip()
    # First non-empty line of stderr is usually the cause
    first_line = next((ln for ln in stderr.splitlines() if ln.strip()), "")
    if first_line:
        return f"{name}: {first_line[:120]}"
    return name


def _kill_process_tree(proc: asyncio.subprocess.Process) -> None:
    """Kill a subprocess and ALL its descendants.

    Claude CLI spawns deep trees (claude → bash → python → WINWORD/LibreOffice).
    On Windows, TerminateProcess only kills the direct child, leaving orphans
    that hold GUI modal dialogs (e.g. Word's "save changes?" prompt) open
    forever. Use taskkill /T (tree) on Windows or killpg on Unix to tear down
    the whole subtree.
    """
    if proc.returncode is not None:
        return
    pid = proc.pid
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            )
        else:
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
    except Exception:
        try:
            proc.kill()
        except ProcessLookupError:
            pass

from config.settings import settings
from app.channel.base import ProgressSnap, UserTarget
from app.channel.registry import get_channel
from app.models.schemas import AgentResult, ToolCallRecord
from app.audit.logger import audit_logger
from app.profiles import load_profile_env, MYCLAW_ROOT, CONFIG_DIR

logger = logging.getLogger("myclaw.cli_loop")

# Map claude_session_id -> {open_id, approval_mode}
# Used by hooks router to look up user + approval mode for tool cards.
session_registry: dict[str, dict] = {}


# ---- helpers ----


def _build_env(workspace: str, profile_name: str = "") -> dict:
    env = os.environ.copy()
    for key in ["CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT",
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"]:
        env.pop(key, None)
    # Inject active profile env vars (ANTHROPIC_BASE_URL, AUTH_TOKEN,
    # model names) per-process so concurrent claude invocations under
    # different profiles don't fight over ~/.claude/settings.json.
    env.update(load_profile_env(profile_name))

    if os.name == "nt" and "CLAUDE_CODE_GIT_BASH_PATH" not in env:
        for candidate in [
            r"D:\Git\bin\bash.exe",
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Git\bin\bash.exe",
        ]:
            if os.path.isfile(candidate):
                env["CLAUDE_CODE_GIT_BASH_PATH"] = candidate
                break
    return env


def _write_myclaw_settings() -> None:
    """幂等覆写 config/claude_settings.json — myclaw 拥有这个文件，不合并。

    子进程通过 --settings 加载它，优先级高于所有 settings.json 层级。
    每次 spawn 前覆写，自愈：即使用户手改过，下次 spawn 恢复预期配置。
    """
    settings_file = CONFIG_DIR / "claude_settings.json"
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    hook_script = MYCLAW_ROOT / "scripts" / "hooks" / "pre_tool_use.py"
    payload = {
        "permissions": {
            "allow": [
                "Bash(*)", "Write(*)", "Edit(*)", "NotebookEdit(*)",
                "Read(*)", "Glob(*)", "Grep(*)", "WebSearch", "WebFetch",
            ],
            "deny": [],
        },
        "hooks": {
            "PreToolUse": [{
                "matcher": "",
                "hooks": [{
                    "type": "command",
                    "command": f'python "{hook_script}"',
                    "timeout": 1800,
                }],
            }],
        },
        "skipDangerousModePermissionPrompt": True,
    }
    settings_file.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), "utf-8",
    )


def _strip_myclaw_hooks(workspace: str) -> None:
    """移除 workspace/.claude/settings.local.json 里 myclaw 注入过的 hook 条目。

    按 hook script 路径子串匹配；只清自己注入的，不动用户其他配置。
    文件清空到 {} 才删，否则保留（用户可能还有 permissions 等 key）。
    """
    from pathlib import Path

    f = Path(workspace) / ".claude" / "settings.local.json"
    if not f.exists():
        return
    try:
        data = json.loads(f.read_text("utf-8"))
    except Exception:
        return
    pre = data.get("hooks", {}).get("PreToolUse")
    if not pre:
        return
    needle = str(MYCLAW_ROOT / "scripts" / "hooks" / "pre_tool_use.py")
    kept = [
        e for e in pre
        if not any(needle in h.get("command", "") for h in e.get("hooks", []))
    ]
    if kept == pre:
        return
    if kept:
        data.setdefault("hooks", {})["PreToolUse"] = kept
    else:
        data.get("hooks", {}).pop("PreToolUse", None)
        if not data.get("hooks"):
            data.pop("hooks", None)
    if data == {}:
        f.unlink()
    else:
        f.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
    logger.info("Stripped myclaw hooks from %s", f)


async def _ensure_hook_config(workspace: str) -> None:
    """每次 spawn 前调用：写 myclaw 自有 settings + 清 workspace 旧注入。

    第三步清理 myclaw 项目自己的 .claude/settings.local.json（旧版本代码
    注入 hook 的地方）。当前该文件只有 permissions 没 hook，是 no-op；保留
    这步以应对历史残留。按脚本路径匹配，不会误删开发者自配的 hook。
    """
    _write_myclaw_settings()
    _strip_myclaw_hooks(workspace)
    _strip_myclaw_hooks(str(MYCLAW_ROOT))


def _has_native_session_history(workspace: str) -> bool:
    """检查指定工作区目录在 Claude Code 中是否有已存在的物理历史 Session 文件。"""
    try:
        ws_path = Path(workspace).resolve()
        # 1. 检查工作区本地是否有 .claude/ 目录且非空
        local_claude = ws_path / ".claude"
        if local_claude.is_dir() and any(local_claude.iterdir()):
            return True

        # 2. 检查全局 ~/.claude/projects/ 下是否有该工作区的历史记录
        home_claude = Path.home() / ".claude" / "projects"
        if home_claude.is_dir():
            sanitized = str(ws_path).replace(":", "").replace("\\", "_").replace("/", "_")
            for proj_dir in home_claude.iterdir():
                if proj_dir.is_dir() and sanitized.lower() in proj_dir.name.lower():
                    if any(proj_dir.glob("*.jsonl")):
                        return True
    except Exception as e:
        logger.debug("Error checking workspace history for %s: %s", workspace, e)
    return False


# ---- main class ----


class ClaudeCLILoop:
    """Manage a long-lived Claude Code CLI subprocess per user.

    The process runs with --print --input-format stream-json so it stays
    alive between messages.  Each user message is written to stdin as a
    JSONL ``user`` event and the background reader resolves the matching
    future when a ``result`` event arrives.

    Each pending message has its own *msg_state* dict (text accumulator,
    tools list, streaming card id) stored alongside the future, so
    concurrent / multi-turn state never leaks between messages.
    """

    def __init__(self) -> None:
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._stdin_writers: dict[str, asyncio.StreamWriter] = {}
        # 会话 key -> 回复目标（平台+群/私路由）。崩溃告警等后续发送用它路由。
        self._targets: dict[str, UserTarget] = {}
        # 会话 key -> 真实 open_id。key 是"群=chat_id / 私聊=p:open_id"（私聊与
        # 群聊进程/记忆完全隔离），但进度卡片、审批卡片、崩溃私聊告警仍要发给
        # 真实的 open_id，查这张表还原。
        self._owners: dict[str, str] = {}
        # list of (future, msg_state) — one entry per pending message
        self._response_futures: dict[str, list[tuple[asyncio.Future, dict]]] = {}
        self._reader_tasks: dict[str, asyncio.Task] = {}
        self._start_locks: dict[str, asyncio.Lock] = {}
        self._last_error: str = ""
        # Store recent AgentResult history per open_id for /notes command lookup
        self._last_results: dict[str, AgentResult] = {}
        self._task_history: dict[str, dict[str, AgentResult]] = {}

    # ---- public API ----

    def get_last_result(self, open_id: str) -> AgentResult | None:
        """Get the most recent task execution result for a user."""
        return self._last_results.get(open_id)

    def get_task_result(self, open_id: str, task_id: str) -> AgentResult | None:
        """Get a specific task execution result by task_id for a user."""
        user_history = self._task_history.get(open_id, {})
        if task_id in user_history:
            return user_history[task_id]
        # Global fallback search across all users if task_id matches
        for history in self._task_history.values():
            if task_id in history:
                return history[task_id]
        return None

    def is_running(self, open_id: str) -> bool:
        """Check if there's a running CLI process that can still accept messages."""
        proc = self._processes.get(open_id)
        writer = self._stdin_writers.get(open_id)
        return (
            proc is not None
            and proc.returncode is None
            and writer is not None
            and not writer.is_closing()
        )

    def get_session_id_for_user(self, open_id: str) -> str | None:
        """Get the Claude session_id for a currently running user process."""
        # registry 里存的是真实 open_id；入参可能是会话 key，两边都试。
        owner = self._owners.get(open_id, open_id)
        for claude_sid, info in session_registry.items():
            if info.get("open_id") in (open_id, owner):
                return claude_sid
        return None

    async def send_and_wait(
        self,
        prompt: str,
        open_id: str,
        workspace: str,
        model: str | None = None,
        approval_mode: str = "m",
        profile_name: str = "",
        claude_session_id: str | None = None,
        resume_session_id: str | None = None,
        target: UserTarget | None = None,
        effort: str = "",
    ) -> AgentResult:
        """Send a prompt to the user's interactive Claude process.

        *open_id* 在这里是"会话 key"（群=g:chat_id:open_id / 私聊=p:open_id），
        同群每人、私聊与群聊各起各的进程互不串台；*target* 是消息来源的
        平台回复目标（进度卡、崩溃告警按它路由）；*effort* 透传
        claude --effort（空 = CLI 默认）。

        Starts the process on first call (or after a crash).  Session
        handling uses claude's native flags:

        - *resume_session_id*  → ``claude --resume <id>`` (specific session)
        - *claude_session_id*  → ``claude --resume <id>``    (persisted session)
        - neither              → fresh session
        """
        lock = self._start_locks.setdefault(open_id, asyncio.Lock())
        chosen = model or settings.claude_default_model

        async with lock:
            # Start on first use or after an actual process failure. A healthy
            # stream-json process remains attached to the Feishu user so
            # subsequent messages stay in the same native Claude session.
            if not self.is_running(open_id):
                await self._teardown_previous(open_id)
                await self._start_process(
                    open_id, workspace, model, approval_mode, profile_name,
                    claude_session_id, resume_session_id, effort=effort,
                )

            writer = self._stdin_writers.get(open_id)
            if not writer or writer.is_closing():
                return self._error_result(
                    prompt, "stdin writer not available (process may have crashed)",
                )

            # Per-message state: single persistent progress surface, event-driven updates.
            # Design rationale: previous design opened a new card every 3000 chars of
            # streamed text, producing 4+ fragmented cards per long task. Now one
            # surface transits running → (retrying/awaiting) → completed/failed/
            # cancelled, refreshed only on meaningful events (tool_use / attachment /
            # api_retry / result). 节流与刷新形态（飞书 PATCH / 企微流式）由
            # 平台的 ProgressHandle 实现负责。
            msg_state = {
                # Progress surface
                "progress": None,
                "model": chosen,
                # Progress counters
                "step": 0,
                "tool_counts": {},
                "warnings": 0,
                "last_warning": "",
                "started_at": asyncio.get_event_loop().time(),
                # Tool/thinking display state
                "current_tool": "",
                "current_tool_args": "",
                "current_text": "",      # text accumulated since last tool_use boundary
                "last_text": "",         # snapshot of last completed text block
            }

            # Open the progress surface (running state, empty body) on the
            # origin platform. 群聊发起的任务，进度发回群里（target 路由）。
            self._targets[open_id] = target or UserTarget(
                platform="feishu", user_id=self._owners.get(open_id, open_id),
            )
            self._owners[open_id] = self._targets[open_id].user_id or open_id
            try:
                channel = get_channel(self._targets[open_id].platform)
                msg_state["progress"] = await channel.open_progress(
                    self._targets[open_id],
                    ProgressSnap(model=chosen, status="running"),
                )
            except Exception as e:
                logger.warning("Failed to create progress surface: %s", e)

            # Queue future + msg_state BEFORE writing stdin so the reader
            # always finds msg_state for the very first stream_event.
            future: asyncio.Future[AgentResult] = asyncio.get_event_loop().create_future()
            self._response_futures.setdefault(open_id, []).append((future, msg_state))

            # Write JSONL user message to stdin
            try:
                payload = json.dumps({
                    "type": "user",
                    "message": {"role": "user", "content": prompt},
                }) + "\n"
                writer.write(payload.encode())
                await writer.drain()
            except (ConnectionResetError, BrokenPipeError, OSError) as exc:
                logger.warning("Stdin writer broken for open_id %s: %s", open_id, exc)
                self._cleanup(open_id)
                return self._error_result(prompt, f"进程通信中断 ({type(exc).__name__}): {exc}")

        return await future

    async def _teardown_previous(self, open_id: str) -> None:
        """Tear down any previous process for this user and wait for its
        reader task to finish cleanup.

        Must run before _start_process when starting a fresh process: the
        old reader's _cleanup pops state keyed by open_id, so if we let it
        run concurrently with a new _start_process it would wipe the new
        process's entries.
        """
        old_task = self._reader_tasks.pop(open_id, None)
        old_proc = self._processes.pop(open_id, None)
        self._stdin_writers.pop(open_id, None)

        if old_proc is not None and old_proc.returncode is None:
            _kill_process_tree(old_proc)

        if old_task is not None and not old_task.done():
            try:
                await asyncio.wait_for(old_task, timeout=3.0)
            except asyncio.TimeoutError:
                if old_proc is not None and old_proc.returncode is None:
                    _kill_process_tree(old_proc)
                old_task.cancel()
            except asyncio.CancelledError:
                pass

    def cancel_by_user(self, open_id: str) -> bool:
        """Kill the running process for a user (synchronous)."""
        proc = self._processes.pop(open_id, None)
        # Resolve any pending futures as cancelled
        entries = self._response_futures.pop(open_id, [])
        for future, _ in entries:
            if not future.done():
                future.set_result(self._error_result("", "process terminated", "cancelled"))

        # Cancel reader task (it will run _cleanup in its finally block)
        task = self._reader_tasks.pop(open_id, None)
        if task and not task.done():
            task.cancel()

        self._stdin_writers.pop(open_id, None)
        self._owners.pop(open_id, None)

        if proc and proc.returncode is None:
            _kill_process_tree(proc)
            return True
        return False

    async def cancel_and_wait(self, open_id: str) -> bool:
        """Kill process and wait for reader task cleanup.

        Use this in async contexts where you need to start a new process
        immediately afterwards (e.g. /model, /stop auto-continue).
        """
        cancelled = self.cancel_by_user(open_id)
        # Small delay for reader task to process EOF and run _cleanup
        await asyncio.sleep(0.3)
        return cancelled

    def cancel(self, task_id: str) -> bool:
        """Not used (open_id based).  Kept for compatibility."""
        logger.warning("cancel(task_id) is deprecated; use cancel_by_user(open_id)")
        return False

    # ---- internal ----

    async def _start_process(
        self, open_id: str, workspace: str, model: str | None,
        approval_mode: str, profile_name: str = "",
        claude_session_id: str | None = None,
        resume_session_id: str | None = None,
        effort: str = "",
    ) -> None:
        chosen = model or settings.claude_default_model
        cli_path = settings.claude_cli_path

        workspace_path = Path(workspace)
        if not workspace_path.is_dir():
            raise NotADirectoryError(f"Workspace is not a directory: {workspace}")
        workspace = str(workspace_path.resolve())

        # shutil.which resolves bare names ("claude") to a real path,
        # picking up .cmd/.bat/.exe on Windows that CreateProcess alone
        # won't auto-append. Without this, asyncio.create_subprocess_exec
        # raises FileNotFoundError (WinError 2) for "claude".
        resolved = shutil.which(cli_path)
        if not resolved:
            raise FileNotFoundError(
                f"Claude CLI not found: {cli_path!r}. "
                f"Install it (`npm i -g @anthropic-ai/claude-code`) or set "
                f"CLAUDE_CLI_PATH to an absolute path."
            )

        args = [
            resolved,
            "--print",
            "--output-format", "stream-json",
            "--input-format", "stream-json",
            "--verbose",
            "--include-partial-messages",
            # 隔离 desktop 端 ~/.claude/settings.json：只加载 project+local 级，
            # 再用 --settings 显式叠加 myclaw 自有配置（hook + permissions），
            # --settings 优先级最高，覆盖一切重叠 key。
            "--setting-sources", "project,local",
            "--settings", str(CONFIG_DIR / "claude_settings.json"),
        ]
        # Resume the exact persisted session after a real process restart.
        # The sentinel is reserved for the explicit /continue command.
        if resume_session_id:
            args.extend(["--resume", resume_session_id])
        elif claude_session_id == "__continue__":
            if _has_native_session_history(workspace):
                args.append("--continue")
            else:
                logger.info("Workspace %s has no native session history, starting fresh clean session.", workspace)
        elif claude_session_id:
            args.extend(["--resume", claude_session_id])
        # Low-risk auto mode (formerly "h"): skip myclaw's hook entirely
        # and let claude's native bypassPermissions handle everything.
        if approval_mode == "l":
            args.extend(["--permission-mode", "bypassPermissions"])
        if chosen:
            args.extend(["--model", chosen])
        if effort:
            args.extend(["--effort", effort])

        env = _build_env(workspace, profile_name)
        await _ensure_hook_config(workspace)

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workspace,
            env=env,
            limit=10 * 1024 * 1024,
            start_new_session=(sys.platform != "win32"),
        )
        self._processes[open_id] = proc
        self._stdin_writers[open_id] = proc.stdin  # type: ignore[assignment]
        logger.warning(
            "Interactive Claude CLI started: open_id=%s workspace=%s model=%s effort=%s pid=%s",
            open_id, workspace, chosen, effort or "(default)", proc.pid,
        )

        # Start background reader
        task = asyncio.create_task(
            self._read_loop(open_id, proc, approval_mode),
        )
        self._reader_tasks[open_id] = task

    # ---- helpers for _read_loop ----

    @staticmethod
    def _current_msg_state(entries: list) -> dict | None:
        """Return the msg_state of the oldest pending message, if any."""
        if entries:
            return entries[0][1]
        return None

    async def _patch_progress(self, msg_state: dict, status: str, *, warning_event: bool = False) -> None:
        """Push a progress snapshot to the platform handle.

        Best-effort：节流与发送异常都由 ProgressHandle 实现吞掉，
        过期进度远好过让任务失败。
        """
        progress = msg_state.get("progress")
        if progress is None:
            return
        snap = self._snapshot(msg_state, status)
        snap.warning_event = warning_event
        await progress.update(snap)

    @staticmethod
    def _snapshot(msg_state: dict, status: str) -> ProgressSnap:
        now = asyncio.get_event_loop().time()
        last_text = msg_state.get("current_text") or msg_state.get("last_text", "")
        return ProgressSnap(
            model=msg_state.get("model", ""),
            status=status,
            step=msg_state.get("step", 0),
            tool_counts=msg_state.get("tool_counts", {}),
            elapsed_s=now - msg_state.get("started_at", now),
            warnings=msg_state.get("warnings", 0),
            current_tool=msg_state.get("current_tool", ""),
            current_tool_args=msg_state.get("current_tool_args", ""),
            last_text=last_text,
            last_warning=msg_state.get("last_warning", ""),
        )

    # ---- reader loop ----

    async def _read_loop(
        self, open_id: str, proc: asyncio.subprocess.Process, approval_mode: str,
    ) -> None:
        """Background task: read JSONL from stdout, dispatch events, resolve futures."""
        stderr_lines: list[str] = []
        model: str = "Claude Code"
        task_id = uuid.uuid4().hex[:12]

        # ---- stderr drainer ----
        async def _drain_stderr() -> None:
            pipe = proc.stderr
            if pipe is None:
                return
            while True:
                line = await pipe.readline()
                if not line:
                    break
                decoded = line.decode("utf-8", errors="replace").rstrip()
                stderr_lines.append(decoded)
                logger.warning("claude stderr: %s", decoded)
        stderr_task = asyncio.create_task(_drain_stderr())

        try:
            while True:
                pipe = proc.stdout
                if pipe is None:
                    break
                raw_line = await pipe.readline()
                if not raw_line:
                    break  # process exited
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("Non-JSON line: %s", line[:200])
                    continue

                etype = event.get("type", "")
                entries = self._response_futures.get(open_id, [])
                msg_state = self._current_msg_state(entries)

                # --- system/init ---
                if etype == "system" and event.get("subtype") == "init":
                    claude_sid = event.get("session_id", "")
                    if claude_sid:
                        target = self._targets.get(open_id)
                        session_registry[claude_sid] = {
                            "open_id": self._owners.get(open_id, open_id),
                            "approval_mode": approval_mode,
                            "chat_id": target.chat_id if target else "",
                            # hooks 发审批卡 / 超时提醒按它路由回发起平台
                            "platform": target.platform if target else "feishu",
                            "target": target,
                            # 进程/状态表按会话 key（g:群:人 / p:人）注册，
                            # hooks 拒绝后取消进程必须用同一个 key
                            "skey": open_id,
                        }
                    model = event.get("model", model)
                    if msg_state:
                        msg_state["model"] = model
                        await self._patch_progress(msg_state, "running")

                # --- stream_event (text deltas accumulate silently; no PATCH) ---
                elif etype == "stream_event":
                    delta = event.get("event", {}).get("delta", {})
                    if msg_state and delta.get("type") == "text_delta":
                        msg_state["current_text"] += delta.get("text", "")

                # --- assistant: tool_use boundary triggers PATCH ---
                elif etype == "assistant":
                    if not msg_state:
                        continue
                    for block in event.get("message", {}).get("content", []):
                        btype = block.get("type")
                        if btype == "text":
                            # Some providers skip streaming and only emit a final
                            # assistant text block. Accumulate only if empty.
                            if not msg_state["current_text"]:
                                msg_state["current_text"] = block.get("text", "")
                        elif btype == "tool_use":
                            tool_name = block.get("name", "unknown")
                            # Boundary: archive the preceding thinking text, start fresh
                            if msg_state["current_text"]:
                                msg_state["last_text"] = msg_state["current_text"]
                                msg_state["current_text"] = ""
                            msg_state["step"] += 1
                            msg_state["tool_counts"][tool_name] = msg_state["tool_counts"].get(tool_name, 0) + 1
                            idx = msg_state["tool_counts"][tool_name]
                            msg_state["current_tool"] = f"{tool_name} #{idx}"
                            msg_state["current_tool_args"] = _summarize_tool_args(
                                tool_name, block.get("input", {})
                            )
                            # Record tool call for audit/final card
                            if not hasattr(msg_state, "_tools"):
                                msg_state["tools"] = msg_state.get("tools", [])
                            msg_state.setdefault("tools", []).append(
                                ToolCallRecord(
                                    tool_name=tool_name,
                                    arguments=block.get("input", {}),
                                ),
                            )
                            await self._patch_progress(msg_state, "running")

                # --- user (tool results): no PATCH, just internal state ---
                elif etype == "user":
                    pass

                # --- attachment (hook errors, etc.): increment warnings, PATCH ---
                elif etype == "attachment":
                    if msg_state and event.get("attachment", {}).get("type") in (
                        "hook_non_blocking_error", "hook_blocking_error",
                    ):
                        msg_state["warnings"] += 1
                        msg_state["last_warning"] = _extract_warning_summary(
                            event.get("attachment", {})
                        )
                        # 告警风暴的 2s 重节流由 ProgressHandle 按 warning_event 处理
                        await self._patch_progress(msg_state, "running", warning_event=True)

                # --- result ---
                elif etype == "result":
                    self._dispatch_result(event, open_id, task_id, stderr_lines, entries)
                    task_id = uuid.uuid4().hex[:12]

                # --- system/api_retry ---
                elif etype == "system" and event.get("subtype") == "api_retry":
                    attempt = event.get("attempt", 0)
                    max_retries = event.get("max_retries", "?")
                    logger.warning("Claude API retry: attempt=%d", attempt)
                    if msg_state:
                        msg_state["last_warning"] = f"API 重试 {attempt}/{max_retries}"
                        await self._patch_progress(msg_state, "retrying")

            # ---- process exited (stdout EOF) ----
            await proc.wait()
            exit_code = proc.returncode or 0
            self._last_error = ""
            if stderr_lines:
                self._last_error = f"exit={exit_code}\n" + "\n".join(stderr_lines[-10:])

            # Resolve any remaining pending futures as failed
            entries = self._response_futures.pop(open_id, [])
            for future, msg_state in entries:
                if not future.done():
                    reason = self._last_error or f"Claude CLI exited (code={exit_code})"
                    future.set_result(self._error_result("", reason))
                    # Update progress surface with error if it exists
                    # （error() 内部吞异常，过期进度好过中断任务）
                    if msg_state.get("progress") is not None:
                        await msg_state["progress"].error("Claude CLI 已退出", reason)

            # 如果当前进程依然是注册进程，说明是发生了非预期的意外崩溃
            is_unexpected = (self._processes.get(open_id) == proc)
            if is_unexpected and (exit_code != 0 or self._last_error):
                reason = self._last_error or f"进程异常退出 (退出码={exit_code})"
                crash_target = self._targets.get(open_id)
                if crash_target is not None:
                    crash_msg = (
                        f"🚨 **Claude 运行进程异常退出** 🚨\n"
                        f"退出状态码: `{exit_code}`\n"
                        f"错误详情:\n```\n{reason[:1000]}\n```\n"
                        f"💡 自愈提示：您可以尝试发送 `/new` 重置会话，或发送 `/cd` 切换到其他可用工作区。"
                    )
                    try:
                        asyncio.create_task(
                            get_channel(crash_target.platform).send_text(crash_target, crash_msg),
                        )
                    except Exception as e:
                        logger.warning("Failed to send crash alert: %s", e)

            logger.info(
                "Claude interactive process exited: open_id=%s code=%s",
                open_id, exit_code,
            )

        except asyncio.CancelledError:
            _kill_process_tree(proc)
            await proc.wait()
        except Exception as exc:
            logger.exception("Claude read loop error: open_id=%s", open_id)
            # Resolve remaining futures
            entries = self._response_futures.pop(open_id, [])
            for future, _ in entries:
                if not future.done():
                    future.set_result(self._error_result("", str(exc)))
        finally:
            stderr_task.cancel()
            self._cleanup(open_id)

    def _dispatch_result(
        self, event: dict, open_id: str, task_id: str,
        stderr_lines: list[str], entries: list,
    ) -> None:
        """Handle a single ``result`` event — resolve the oldest pending future."""
        if not entries:
            logger.debug(
                "Result event with no waiter: open_id=%s task=%s", open_id, task_id,
            )
            return

        future, msg_state = entries.pop(0)

        subtype = event.get("subtype", "")
        cost = event.get("total_cost_usd", 0)
        duration_ms = event.get("duration_ms", 0)
        duration_s = duration_ms / 1000.0
        num_turns = event.get("num_turns", 0)
        error = event.get("error", "")
        usage = event.get("usage", {})

        text = msg_state.get("current_text", "") or msg_state.get("last_text", "")
        tools = msg_state.get("tools", [])
        tool_counts = msg_state.get("tool_counts", {})
        tool_count = sum(tool_counts.values())
        status = "failed" if subtype.startswith("error") else "completed"
        result_text = event.get("result", "") or text

        result = AgentResult(
            task_id=task_id,
            prompt="",
            model=event.get("model", "unknown"),
            status=status,
            text=result_text,
            tools_used=tools,
            error=error if subtype == "error" else "",
            session_id=event.get("session_id", "") or self.get_session_id_for_user(open_id) or "",
            cost_usd=cost,
            duration_s=duration_s,
            num_turns=num_turns,
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            started_at=datetime.now(),
            finished_at=datetime.now(),
        )

        # Record result in history for /notes command lookup
        self._last_results[open_id] = result
        user_history = self._task_history.setdefault(open_id, {})
        user_history[task_id] = result
        if len(user_history) > 50:
            first_key = next(iter(user_history))
            user_history.pop(first_key, None)

        # Transition progress surface → final state (same surface, no new card).
        progress = msg_state.get("progress")
        if progress is not None:
            final_status = "cancelled" if status == "cancelled" else status
            elapsed = asyncio.get_event_loop().time() - msg_state.get("started_at", asyncio.get_event_loop().time())
            final_snap = ProgressSnap(
                model=msg_state.get("model", event.get("model", "")),
                status=final_status,
                step=msg_state.get("step", 0),
                tool_counts=tool_counts,
                elapsed_s=elapsed,
                warnings=msg_state.get("warnings", 0),
                result_text=result_text,
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                error=error,
                session_id=result.session_id,
                task_id=task_id,
            )
            # finish() 内部吞异常（过期进度好过中断任务）
            asyncio.create_task(progress.finish(final_snap))

        if not future.done():
            future.set_result(result)

        audit_logger.log_agent_result(
            task_id=task_id,
            model="claude-code",
            status=status,
            tools_count=tool_count,
            text_len=len(result_text),
        )

        logger.info(
            "Claude done: task=%s status=%s cost=$%.4f time=%.1fs tools=%d",
            task_id, status, cost, duration_s, tool_count,
        )

    def _cleanup(self, open_id: str) -> None:
        """Clean up all state for a user after process exit or error."""
        self._processes.pop(open_id, None)
        self._stdin_writers.pop(open_id, None)
        self._reader_tasks.pop(open_id, None)
        # Clean up session_registry entries for this user
        # （registry 存真实 open_id，入参是会话 key，两边都清）
        owner = self._owners.pop(open_id, open_id)
        stale_sids = [
            sid for sid, info in session_registry.items()
            if info.get("open_id") in (open_id, owner)
        ]
        for sid in stale_sids:
            del session_registry[sid]

    @staticmethod
    def _error_result(prompt: str, error: str, status: str = "failed") -> AgentResult:
        return AgentResult(
            task_id=uuid.uuid4().hex[:12],
            prompt=prompt,
            model="unknown",
            status=status,
            error=error,
            started_at=datetime.now(),
            finished_at=datetime.now(),
        )


# Singleton
claude_cli_loop = ClaudeCLILoop()
