"""Create CLI-born Claude Code sessions via Windows PTY.

Why this exists:
    When myclaw launches claude with SDK flags (--print --output-format
    stream-json --input-format stream-json --verbose), Claude Code tags
    every message in the .jsonl with ``entrypoint: sdk-cli``. The local
    terminal picker filters sdk-cli sessions out, so myclaw-created
    sessions are invisible to users running ``claude`` interactively.

    By PTY-spawning claude WITHOUT SDK flags, Claude Code writes
    ``entrypoint: cli`` natively, and the session becomes picker-visible.
    Once such a cli-born stub exists, myclaw can resume it via SDK mode
    ``--resume <stub-id>`` for actual task execution — appended sdk-cli
    records don't change picker visibility once the first message is cli.

Windows-only:
    Uses ``pywinpty``. myclaw is currently Windows-only (see scripts/tray.pyw),
    so this is consistent. On non-Windows the function raises RuntimeError
    so callers can fall back to legacy behavior.

Flow:
    PTY.spawn(cmd.exe /c claude.cmd, cwd=workspace)
    -> drain startup ~10s (TUI render + MCP init)
    -> write placeholder prompt + \\r
    -> poll ~/.claude/projects/<enc-cwd>/*.jsonl for marker
    -> return session_id (file stem)
    -> taskkill /T /F the PTY process tree
"""
from __future__ import annotations

import logging
import os
import platform
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

logger = logging.getLogger("myclaw.pty_stub")

# Single-char placeholder keeps the first user message small in the resumed
# conversation history. Claude will respond briefly; the user's real first
# task becomes the second turn.
_PLACEHOLDER_PROMPT = "."

# How long to wait for Claude TUI splash + MCP init before sending the prompt.
_STARTUP_DRAIN_S = 10.0

# How long to wait overall for the .jsonl file to appear after submitting.
_TOTAL_TIMEOUT_S = 60.0


def _is_windows() -> bool:
    return platform.system() == "Windows"


def _resolve_pty_command() -> tuple[str, str]:
    """Return (appname, cmdline) for PTY.spawn(). Wraps .cmd/.bat shims."""
    claude = shutil.which("claude")
    if not claude:
        raise FileNotFoundError(
            "claude not found on PATH. Install @anthropic-ai/claude-code."
        )
    claude_path = Path(claude).resolve()
    suffix = claude_path.suffix.lower()
    if suffix in {".cmd", ".bat"}:
        comspec = os.environ.get("COMSPEC") or shutil.which("cmd.exe")
        if not comspec:
            raise RuntimeError("Cannot locate cmd.exe via COMSPEC or PATH")
        appname = str(Path(comspec).resolve())
        # cmd.exe /d /s /c with nested quotes around the shim path.
        cmdline = f'"{appname}" /d /s /c ""{claude_path}""'
        return appname, cmdline
    if suffix == ".exe":
        return str(claude_path), f'"{claude_path}"'
    raise RuntimeError(f"Unsupported claude launcher: {claude_path} (suffix={suffix!r})")


def _drain(pty, duration: float) -> str:
    """Non-blocking read loop. Returns accumulated output."""
    deadline = time.monotonic() + duration
    chunks: list[str] = []
    while time.monotonic() < deadline:
        try:
            data = pty.read(blocking=False)
        except Exception:
            if not pty.isalive() or getattr(pty, "iseof", lambda: False)():
                break
            time.sleep(0.05)
            continue
        if data:
            if isinstance(data, bytes):
                data = data.decode("utf-8", errors="replace")
            chunks.append(data)
        else:
            time.sleep(0.05)
    return "".join(chunks)


def _encode_cwd(cwd: str) -> str:
    """Match Claude Code's project-dir encoding."""
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def _find_session_file(workspace: str, marker: str, started_at: float) -> Path | None:
    marker_bytes = marker.encode("utf-8")
    project_dir = Path.home() / ".claude" / "projects" / _encode_cwd(workspace)
    if not project_dir.exists():
        return None
    for path in project_dir.glob("*.jsonl"):
        try:
            stat = path.stat()
            # Allow 2s filesystem mtime rounding tolerance.
            if stat.st_mtime < started_at - 2:
                continue
            with path.open("rb") as fh:
                if marker_bytes in fh.read():
                    return path
        except (OSError, PermissionError):
            continue
    return None


def _kill_tree(pid: int) -> None:
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True, timeout=10,
        )
    except Exception as e:
        logger.warning("taskkill failed for pid=%s: %s", pid, e)


def create_cli_born_session(
    workspace: str,
    startup_drain_s: float = _STARTUP_DRAIN_S,
    total_timeout_s: float = _TOTAL_TIMEOUT_S,
) -> str:
    """Create a cli-born Claude Code session in ``workspace``.

    已升级为跨平台原生文件规范注水实现（见 app.agent.session_sync）。
    0 秒等待、0 外部依赖、100% 兼容 macOS 与 Windows。
    """
    from app.agent.session_sync import create_cli_born_session as _create_native
    return _create_native(workspace)

