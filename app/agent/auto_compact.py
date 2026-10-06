"""会话超窗自动压缩：用 claude 自身作为压缩器（2026-10-03 实测定案）。

背景：`claude -p --resume <超大会话>` 在供应商窗口不足时直接把
"Prompt is too long" 当作最终结果透传——CLI 的自动压缩在此路径不触发，
`CLAUDE_CODE_MAX_CONTEXT_TOKENS` 旋钮也无效（引擎无本地超限预估，
实测两次均原样报错）。因此由网关编排压缩：

1. 终态文本命中 PROMPT_TOO_LONG 且本次是 resume/continue；
2. 取该会话 transcript 末段（约 60-80K token，低于所有已配供应商窗口）；
3. spawn 一次性 `claude -p --no-session-persistence` 提炼交接摘要；
4. 以摘要为背景、在新会话重放用户请求（完整走正常进度/审批链路）。

压缩失败时回退为 TOO_LONG_GUIDANCE 指引文本（B 兜底）。
"""
from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path

PROMPT_TOO_LONG = re.compile(
    r"prompt is too long"
    r"|context[_ ]?(?:window|length)[_ ]?(?:exceeded|limit)"
    r"|maximum context length"
    r"|too many (?:input )?tokens"
    r"|exceeds (?:the )?context",
    re.IGNORECASE,
)

TOO_LONG_GUIDANCE = (
    "\n\n⚠️ 该会话累计上下文已超过当前模型窗口，自动压缩未成功。可选：\n"
    "1) `/model` 切换 glm（1M 窗口）后重发本条\n"
    "2) `/reset` 开启新会话\n"
    "3) `/resume` 选择较小的历史会话"
)

_TAIL_CHARS = 240_000  # ≈ 60-80K token，低于所有已配供应商窗口（256K~1M）

_COMPACT_INSTRUCTION = (
    "你是会话压缩器。下面是一个 Claude Code 超长会话的末段记录（JSONL）。"
    "忽略 JSON 元数据与工具输出噪声，提炼一份交接摘要，1200 字以内，覆盖："
    "1) 任务与背景 2) 关键决策与结论 3) 正在进行的事项 4) 下一步建议 5) 重要约束与坑。"
    "直接输出摘要正文。\n\n<log>\n{tail}\n</log>"
)


def find_transcript(session_id: str) -> Path | None:
    """定位 <session_id>.jsonl（CLAUDE_SESSION_DIR 优先，回退 ~/.claude/projects）。"""
    from config.settings import settings

    roots: list[Path] = []
    raw = settings.get_claude_session_dir()
    if raw:
        roots.append(Path(raw).expanduser())
    roots.append(Path.home() / ".claude" / "projects")
    for root in roots:
        try:
            for hit in root.glob(f"**/{session_id}.jsonl"):
                return hit
        except Exception:
            continue
    return None


async def summarize_tail(
    transcript: Path,
    *,
    cli_path: str,
    model: str,
    env: dict,
    workspace: str,
    timeout_s: int = 300,
) -> str | None:
    """一次性 claude 进程提炼末段摘要；失败/超时/输出异常返回 None。"""
    try:
        text = transcript.read_text(encoding="utf-8", errors="replace")[-_TAIL_CHARS:]
    except Exception:
        return None
    if len(text) < 2000:
        return None  # 会话本身不大，超窗另有原因，不做压缩
    resolved = shutil.which(cli_path) or cli_path
    instruction = _COMPACT_INSTRUCTION.format(tail=text)
    try:
        proc = await asyncio.create_subprocess_exec(
            resolved, "-p", "--model", model, "--no-session-persistence",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workspace,
            env=env,
        )
        out, _err = await asyncio.wait_for(
            proc.communicate(instruction.encode("utf-8")), timeout=timeout_s
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    summary = out.decode("utf-8", errors="replace").strip()
    if len(summary) < 80 or len(summary) > 20_000:
        return None
    return summary
