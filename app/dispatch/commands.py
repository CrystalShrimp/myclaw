"""共享指令分发（从 app/feishu/events.py 的 _dispatch/_run_claude 搬迁）。

平台事件层（feishu/wecom 的 events）把消息解析成 UserTarget + 文本后
调用 handle_message；所有指令语义、会话/偏好变更、Claude 调度都在这里，
富视图通过 ReplyContext.view(kind) 交给平台渲染。
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import re
import uuid
from pathlib import Path

from app.agent.cli_loop import claude_cli_loop, _kill_process_tree
from app.agent.session_sync import detect_cli_session_update, sync_claude_session_to_cli
from app.audit.logger import audit_logger
from app.channel.base import UserTarget
from app.channel.registry import get_channel
from app.dispatch.context import ReplyContext
from app.dispatch.helpers import (
    _claude_config_path,
    _decode_output,
    _detected_claude_config_path,
    _iter_claude_session_messages,
    _scan_workspace_files,
    append_to_claude_history,
    find_all_claudecode_projects,
    get_project_meta,
    list_workspace_sessions,
    predict_continue_session,
)
from app.dispatch.sessions import session_manager, skey_for
from app.models.schemas import Session, TaskStatus
from app.profiles import discover_profiles, get_active_profile, profile_level_models, test_profile
from app.state.preferences import preferences_manager
from config.settings import settings

logger = logging.getLogger("myclaw.dispatch")


async def _send_view_after_callback(reply: ReplyContext, kind: str, **payload) -> None:
    """Let the platform callback acknowledgement flush before sending a new view."""
    await asyncio.sleep(0.2)
    await reply.view(kind, **payload)


async def _send_text_after_callback(reply: ReplyContext, content: str) -> None:
    await asyncio.sleep(0.2)
    await reply.text(content)


def _workspace_selection_payload(session: Session | None = None) -> dict:
    if session and session.workspace:
        current = session.workspace
    else:
        current = settings.default_workspace.strip() or "尚未设置"
    return {"current": current, "projects": find_all_claudecode_projects()}


async def _check_and_run_pending(target: UserTarget) -> bool:
    """Check if all initial setup items (Provider, Level, Mode) are complete.
    If complete and pending_prompt exists, trigger _run_claude automatically.
    """
    open_id = target.user_id
    chat_id = target.chat_id
    is_group = target.is_group
    preferences = preferences_manager.get(open_id)
    profiles = discover_profiles()

    has_provider = preferences.model in profiles
    has_level = preferences.level in ("haiku", "sonnet", "opus")
    has_mode = preferences.mode in ("h", "m", "l")

    if has_provider and has_level and has_mode:
        session = session_manager.get_user_session(skey_for(target))

        if session and session.pending_prompt.strip():
            pending = session.pending_prompt.strip()
            session.pending_prompt = ""
            session_manager.save_session(session)

            profile_label = profiles.get(preferences.model, {}).get("label", preferences.model)
            mode_labels = {"h": "🛡️ 严格模式 (h)", "m": "⚖️ 平衡模式 (m)", "l": "⚡ 全自动模式 (l)"}
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
            reply = ReplyContext(target)
            await reply.text("\n".join(msg_lines))
            asyncio.get_running_loop().create_task(_run_claude(pending, target, session))
            return True
    return False


# ===== Core dispatch =====


async def handle_message(target: UserTarget, message_id: str, text: str) -> None:
    """入口：一条用户文本消息（已剥 @、已判群/私）。"""
    open_id = target.user_id
    chat_id = target.chat_id
    is_group = target.is_group
    reply = ReplyContext(target)
    channel = get_channel(target.platform)
    allowed_check = channel.is_allowed(target)
    if inspect.isawaitable(allowed_check):
        allowed = await allowed_check
    else:
        allowed = bool(allowed_check)
    if not allowed:
        mode = settings.get_allowed_mode()
        if mode == "groups":
            err_msg = (
                f"🚫 **权限拦截提醒**\n"
                f"• 您的用户 ID: `{open_id or '(空)'}`\n"
                f"• 当前访问模式: `groups`（仅指定群成员可用）\n\n"
                f"💡 **解决建议**：请先加入被授权的群聊；管理员可在 `.env` 的 "
                f"`ALLOWED_GROUP_IDS` 中调整群列表。"
            )
        else:
            allowed_list = settings.get_allowed_users()
            err_msg = (
                f"🚫 **权限拦截提醒**\n"
                f"• 您的用户 ID: `{open_id or '(空)'}`\n"
                f"• 当前允许的用户列表: `{', '.join(allowed_list) if allowed_list else '(空，未配置白名单)'}`\n\n"
                f"💡 **解决建议**：请在 `.env` 中把您的用户 ID 加入 `ALLOWED_USERS`；"
                f"如需对所有用户开放，请把 `.env` 中的 `ALLOWED_USERS` 设为空。"
            )
        await reply.text(err_msg)
        return

    text = text.strip()
    text_lower = text.lower()

    session = session_manager.get_user_session(skey_for(target))
    if _claude_config_path() is None:
        # 配置流程：保持私聊
        await reply.view(
            "claude_dir_setup", detected_path=str(_detected_claude_config_path()),
        )
        return

    # --- /stop: interrupt current task ---
    if text_lower in ("/stop", "停止"):
        cancelled = await claude_cli_loop.cancel_and_wait(skey_for(target))
        if cancelled:
            await reply.text("已中断会话。")
        else:
            await reply.text("没有正在运行的任务。")
        return

    # --- /reset: reset all setup preferences for testing ---
    if text_lower in ("/reset", "重置"):
        preferences = preferences_manager.get(open_id)
        preferences.model = ""
        preferences.level = ""
        preferences.mode = ""
        preferences.effort = ""
        preferences_manager.save(open_id, preferences)
        await reply.text(
            "已清空所有初始设置（Provider / Model Level / Mode / Effort）。",
        )
        return

    # --- /status ---
    if text_lower == "/status":
        try:
            session = session_manager.get_user_session(skey_for(target))
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

                await reply.text(
                    f"📁 工作区: `{session.workspace}`\n"
                    f"🔑 Claude Session: `{csid}`\n"
                    f"🤖 模型供应商: `{preferences.model or '未选择'}`\n"
                    f"⚡ 模型规格: `{preferences.level or '未选择'}`\n"
                    f"🧠 思考力度: `{preferences.effort or 'CLI 默认'}`\n"
                    f"🛡️ 审批模式: `{preferences.mode or '未选择'}`\n"
                    f"💬 底层推演步数: {msg_count} 步 (含思考/工具调用记录)\n"
                    f"{ctx_info}"
                    f"状态: {session.status.value}",
                )
            else:
                await reply.text("没有活跃会话。用 /new 创建新会话。")
        except Exception as err:
            logger.exception("Error handling /status for user %s", open_id)
            await reply.text(f"状态获取失败：{err}")
        return

    # --- /help ---
    if text_lower == "/help":
        await reply.view("help")
        return

    # --- /provider [profile]: choose provider profile ---
    if text_lower == "/provider" or text_lower.startswith("/provider "):
        parts = text.split(None, 1)
        profiles = discover_profiles()
        preferences = preferences_manager.get(open_id)
        if not profiles:
            await reply.text("未发现 config/settings_*.json 模型配置。")
            return
        if len(parts) == 2:
            name = parts[1].strip().lower()
            if name in ("reset", "clear"):
                preferences.model = ""
                preferences.level = ""
                preferences.mode = ""
                preferences_manager.save(open_id, preferences)
                await reply.text(
                    "已清空所有初始设置（Provider / Model Level / Mode）。",
                )
                return
            if name not in profiles:
                await reply.text(f"未知供应商: `{name}`\n可用: {'、'.join(profiles.keys())}")
                return
            preferences.model = name
            preferences_manager.save(open_id, preferences)
            await claude_cli_loop.cancel_and_wait(skey_for(target))
            ok, detail = test_profile(name)
            label = profiles[name].get("label", name)
            await reply.text(
                f"模型供应商已切换为 `{label}`。" if ok else f"模型供应商 `{label}` 不可用: {detail}",
            )
            return

        await reply.view(
            "provider_selection",
            approval_id=uuid.uuid4().hex[:12],
            profiles=profiles,
            active_profile=preferences.model,
        )
        return

    # --- /model [haiku|sonnet|opus]: choose model capability level ---
    if text_lower == "/model" or text_lower.startswith("/model "):
        parts = text.split(None, 1)
        preferences = preferences_manager.get(open_id)
        profiles = discover_profiles()
        valid_levels = ("haiku", "sonnet", "opus")
        # 当前供应商的 档位->实际模型 映射（profile 内 ANTHROPIC_DEFAULT_*_MODEL）
        models_map = profile_level_models(preferences.model)

        if len(parts) == 2:
            arg = parts[1].strip().lower()
            if arg in valid_levels:
                preferences.level = arg
                preferences_manager.save(open_id, preferences)
                await claude_cli_loop.cancel_and_wait(skey_for(target))
                actual = models_map.get(arg, "")
                await reply.text(
                    f"模型规格已切换为 `{arg}`" + (f"（`{actual}`）" if actual else "") + "。"
                )
                return
            elif arg in profiles:
                await reply.text(
                    f"⚠️ `{arg}` 是模型供应商 (Provider)。\n"
                    f"切换供应商请使用：`/provider {arg}`\n"
                    f"切换模型规格请使用：`/model haiku|sonnet|opus`",
                )
                return

        await reply.view(
            "level_selection",
            approval_id=uuid.uuid4().hex[:12],
            current_model=preferences.level,
            models_map=models_map,
        )
        return

    # --- /mode [h|m|l]: choose approval mode ---
    if text_lower == "/mode" or text_lower.startswith("/mode "):
        parts = text.split(None, 1)
        preferences = preferences_manager.get(open_id)
        if len(parts) == 2 and parts[1].strip().lower() in ("h", "m", "l"):
            old_mode = preferences.mode
            new_mode = parts[1].strip().lower()
            preferences.mode = new_mode
            preferences_manager.save(open_id, preferences)
            mode_labels = {"h": "🛡️ 严格模式", "m": "⚖️ 平衡模式", "l": "⚡ 全自动模式"}
            mode_name = mode_labels.get(new_mode, new_mode)
            # session_registry 里的 approval_mode 是进程启动时的快照，
            # 复用旧进程会让 hook 仍按旧 mode 走审批分支，必须 teardown。
            if old_mode != new_mode:
                await claude_cli_loop.cancel_and_wait(skey_for(target))
                await reply.text(
                    f"审批模式已切换为 {mode_name} (`{new_mode}`)，已重启 Claude 进程使新模式生效。",
                )
            else:
                await reply.text(
                    f"审批模式仍为 {mode_name} (`{new_mode}`)。",
                )
            await _check_and_run_pending(target)
            return

        await reply.view(
            "mode_selection",
            approval_id=uuid.uuid4().hex[:12],
            active_mode=preferences.mode,
        )
        return

    # --- /effort [low|medium|high|xhigh|max]: thinking effort (claude --effort) ---
    if text_lower == "/effort" or text_lower.startswith("/effort "):
        parts = text.split(None, 1)
        preferences = preferences_manager.get(open_id)
        valid_efforts = ("low", "medium", "high", "xhigh", "max")

        if len(parts) == 2:
            arg = parts[1].strip().lower()
            if arg in valid_efforts:
                old_effort = preferences.effort
                preferences.effort = arg
                preferences_manager.save(open_id, preferences)
                # effort 是进程启动参数，复用旧进程不会生效，必须 teardown
                if old_effort != arg:
                    await claude_cli_loop.cancel_and_wait(skey_for(target))
                    await reply.text(
                        f"⚡ 思考力度已切换为 `{arg}`，已重启 Claude 进程使新力度生效。",
                    )
                else:
                    await reply.text(f"⚡ 思考力度仍为 `{arg}`。")
                await _check_and_run_pending(target)
                return
            if arg in ("off", "none", "default"):
                preferences.effort = ""
                preferences_manager.save(open_id, preferences)
                await claude_cli_loop.cancel_and_wait(skey_for(target))
                await reply.text("⚡ 思考力度已恢复 CLI 默认。")
                await _check_and_run_pending(target)
                return

        await reply.view(
            "effort_selection",
            approval_id=uuid.uuid4().hex[:12],
            current_effort=preferences.effort,
        )
        return

    # --- /pwd: print current workspace path ---
    if text_lower == "/pwd" or text_lower.startswith("/pwd "):
        session = session_manager.get_user_session(skey_for(target))
        ws = session.workspace if session else settings.get_default_workspace()
        await reply.text(f"📁 当前工作目录：`{ws}`")
        return

    # --- /file: select and send workspace files ---
    if text_lower == "/file" or text_lower.startswith("/file "):
        session = session_manager.get_user_session(skey_for(target))
        ws = session.workspace if session else settings.get_default_workspace()
        files = _scan_workspace_files(ws)
        await reply.view("file_selection", workspace=ws, files=files)
        return

    # --- /cd <path>: switch workspace ---
    if text_lower.startswith("/cd"):
        parts = text.split(None, 1)

        # 1. 如果不带参数，展示卡片选择已有项目或新建
        if len(parts) < 2 or not parts[1].strip():
            session = session_manager.get_user_session(skey_for(target))
            await reply.view("workspace_selection", **_workspace_selection_payload(session))
            return

        # 2. 如果带路径参数，直接校验切换
        new_path = parts[1].strip()
        p = Path(new_path)
        if not p.is_absolute():
            await reply.text("错误：请使用绝对路径，例如 `/cd D:\\projects\\myapp`")
            return

        if not p.exists():
            # 检查父目录是否存在
            parent = p.parent
            if not parent.exists():
                await reply.text(f"❌ 切换失败：父目录 `{parent}` 在磁盘上不存在！")
                return

            # 目录不存在但父目录存在 — 弹出前置确认卡片，待用户确认后再建目录切换
            target_path = str(p.resolve())
            meta = get_project_meta(target_path)
            warning_running = claude_cli_loop.is_running(skey_for(target))
            await reply.view(
                "cd_confirm",
                target_path=target_path,
                is_new=True,
                git_branch=meta["git_branch"],
                claude_md=meta["claude_md"],
                warning_running=warning_running,
            )
            return

        session = session_manager.get_user_session(skey_for(target))
        if not session:
            session = session_manager.create_session(
                skey_for(target), chat_id if is_group else "",
                workspace=str(p.resolve()),
            )
        else:
            session.workspace = str(p.resolve())
            session.workspace_selected = True
            session.claude_session_id = "__continue__"  # 切换工作区后全自动带 --continue 恢复该项目最新 Session 历史
            session_manager.save_session(session)

        resolved_workspace = str(p.resolve())
        # 工作区严格属于当前会话（群聊/私聊各自独立），不再改写全局默认值
        claude_cli_loop.cancel_by_user(skey_for(target))

        pred = predict_continue_session(session.workspace)
        if pred["can_continue"]:
            pred_text = f"\n🔄 **预计关联会话：** 可继续恢复 (`{pred['session_id']}`)\n💬 **上次对话：** {pred['last_summary']}"
        else:
            pred_text = "\n🆕 **预计关联会话：** 纯净项目 (发送首条消息时自动分配新 Session)"

        pref = preferences_manager.get(open_id)
        profiles = discover_profiles()
        profile_label = profiles.get(pref.model, {}).get("label", pref.model) or pref.model or "未设置"
        mode_labels = {"h": "🛡️ 严格模式 (h)", "m": "⚖️ 平衡模式 (m)", "l": "⚡ 全自动模式 (l)"}
        mode_label = mode_labels.get(pref.mode, pref.mode or "未设置")

        await reply.text(
            f"📁 工作区已切换：`{session.workspace}`{pred_text}\n"
            f"⚙️ 当前配置：供应商 `{profile_label}` | 规格 `{pref.level or '未设置'}` | 审批模式 `{mode_label}`"
        )
        return

    # --- /session [session_id]: list sessions in current workspace and resume one ---
    if text_lower == "/session" or text_lower.startswith("/session "):
        parts = text.split(None, 1)
        session = session_manager.get_user_session(skey_for(target))
        if not session:
            session = session_manager.create_session(
                skey_for(target), chat_id if is_group else "",
            )
        ws = session.workspace or settings.get_default_workspace()

        # /session <id>: 直接走 /resume 同款路径
        if len(parts) == 2 and parts[1].strip():
            target_id = parts[1].strip()
            await claude_cli_loop.cancel_and_wait(skey_for(target))
            session.claude_session_id = target_id
            session.context_tokens = 0
            session_manager.save_session(session)
            await reply.text(
                f"🔑 已切换到 Claude Session `{target_id}`。\n"
                "下一条消息将通过 `--resume` 在该会话内继续。",
            )
            return

        # /session (无参): 弹卡片选择
        sessions = list_workspace_sessions(ws)
        cur = session.claude_session_id or ""
        if cur == "__continue__":
            cur = ""
        await reply.view(
            "session_selection", workspace=ws, sessions=sessions, current_session_id=cur,
        )
        return

    # --- /resume <session_id>: switch to one exact native session ---
    if text_lower.startswith("/resume"):
        parts = text.split(None, 1)
        session = session_manager.get_user_session(skey_for(target))
        if len(parts) < 2 or not parts[1].strip():
            cur = session.claude_session_id if session else ""
            await reply.text(
                f"当前 Claude Session: `{cur or '无'}`\n\n用法: `/resume <session_id>`",
            )
            return
        if not session:
            session = session_manager.create_session(
                skey_for(target), chat_id if is_group else "",
            )
        await claude_cli_loop.cancel_and_wait(skey_for(target))
        session.claude_session_id = parts[1].strip()
        session.context_tokens = 0
        session_manager.save_session(session)
        ws_config = preferences_manager.load_workspace_config(session.workspace)
        cur_pref = preferences_manager.get(open_id)
        target_config = ws_config if (ws_config and ws_config.complete) else (cur_pref if cur_pref.complete else None)

        if target_config:
            profiles = discover_profiles()
            profile_label = profiles.get(target_config.model, {}).get("label", target_config.model)
            await reply.text(
                f"🔑 已切换到 Claude Session `{session.claude_session_id}`。",
            )
            await reply.view(
                "ws_config_reuse",
                approval_id=uuid.uuid4().hex[:12],
                workspace=session.workspace,
                profile_label=profile_label,
                level=target_config.level,
                mode=target_config.mode,
                action_type="switch",
            )
        else:
            preferences_manager.clear(open_id)
            await reply.text(
                f"🔑 已切换到 Claude Session `{session.claude_session_id}`。\n"
                "运行参数已清空，请依次设置 `/model`、`/mode`。",
            )
        return

    # --- /continue [prompt]: resume most recent session via native --continue ---
    if text_lower.startswith("/continue"):
        parts = text.split(None, 1)
        session = session_manager.get_user_session(skey_for(target))
        if not session:
            session = session_manager.create_session(
                skey_for(target), chat_id if is_group else "",
            )

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
        await _run_claude(prompt, target, session, skip_classify=True)
        return

    # --- /new: reset native session and runtime preferences ---
    if text_lower == "/new":
        await claude_cli_loop.cancel_and_wait(skey_for(target))
        session = session_manager.reset_user_session(skey_for(target))

        # 跨平台（Mac/Windows）原生生成可在终端 resume 中看到的会话 ID
        try:
            from app.agent.session_sync import create_cli_born_session
            session.claude_session_id = create_cli_born_session(session.workspace)
            session_manager.save_session(session)
        except Exception as e:
            logger.warning("Session sync init failed: %s", e)

        pref = preferences_manager.get(open_id)
        profiles = discover_profiles()
        profile_label = profiles.get(pref.model, {}).get("label", pref.model) or pref.model or "未设置"
        mode_labels = {"h": "🛡️ 严格模式 (h)", "m": "⚖️ 平衡模式 (m)", "l": "⚡ 全自动模式 (l)"}
        mode_label = mode_labels.get(pref.mode, pref.mode or "未设置")

        await reply.text(
            f"✨ **新会话已就绪**\n"
            f"📁 工作区：`{session.workspace}`\n"
            f"⚙️ 当前配置：供应商 `{profile_label}` | 规格 `{pref.level or '未设置'}` | 审批模式 `{mode_label}`\n\n"
            f"💡 全局配置已生效，直接发送消息即可开始对话。"
        )
        return

    # --- /clean: remove old session files, keep only current ---
    if text_lower == "/clean":
        session = session_manager.get_user_session(skey_for(target))
        if session:
            session_manager.clean_old_sessions(skey_for(target))
            await reply.text(
                f"已清理旧会话文件，当前会话: `{session.session_id}`",
            )
        else:
            await reply.text("没有活跃会话。")
        return

    # --- /compact: trigger CLI's built-in compact on current session ---
    if text_lower == "/compact":
        if not settings.compact_enabled:
            await reply.text("压缩功能已禁用。请联系管理员开启。")
            return
        session = session_manager.get_user_session(skey_for(target))
        if not session or not session.claude_session_id:
            await reply.text(
                "没有活跃的 Claude 会话，无法压缩。\n"
                "先发一条消息启动会话，上下文不足时再使用 `/compact`。",
            )
            return
        await reply.text(
            "🧹 已启动上下文压缩 (Compact)，正在为您整理与压缩当前 Session 的上下文历史...",
        )
        await _run_claude("/compact", target, session, skip_classify=True)
        return

    # --- /mem: workspace memo in CLAUDE.md ---
    # `/mem`           → show current content
    # `/mem <text>`    → append a line
    # `/mem clear`     → wipe
    if text_lower == "/mem" or text_lower.startswith("/mem "):
        arg = text[4:].strip()  # text after "/mem"
        session = session_manager.get_user_session(skey_for(target))
        workspace = session.workspace if session else settings.get_default_workspace()
        memory_md = Path(workspace) / "CLAUDE.md"

        # `/mem` (no arg) → list current memory
        if not arg:
            try:
                if memory_md.exists():
                    body = memory_md.read_text("utf-8")
                else:
                    await reply.text(
                        f"📝 当前工作区 `{workspace}` 还没有 CLAUDE.md。\n"
                        f"用法：`/mem <内容>` 追加；`/mem clear` 清空。",
                    )
                    return
                char_count = len(body)
                PREVIEW_LIMIT = 4000
                if char_count <= PREVIEW_LIMIT:
                    preview = body
                    trunc_note = ""
                else:
                    preview = body[:PREVIEW_LIMIT]
                    trunc_note = f"\n\n…（共 {char_count} 字符，仅显示前 {PREVIEW_LIMIT}）"
                await reply.text(
                    f"📝 `{workspace}` 的 CLAUDE.md（{char_count} 字符）：\n```\n{preview}```{trunc_note}",
                )
            except Exception as e:
                await reply.text(f"读取失败: {e}")
            return

        # `/mem clear` → wipe memory file
        if arg.lower() == "clear":
            try:
                if memory_md.exists():
                    memory_md.unlink()
                    await reply.text(f"已清空 `{workspace}` 下的 CLAUDE.md。")
                else:
                    await reply.text(f"`{workspace}` 下没有 CLAUDE.md，无需清空。")
            except Exception as e:
                await reply.text(f"清空失败: {e}")
            return

        # `/mem <text>` → append
        content = arg
        try:
            if memory_md.exists():
                existing = memory_md.read_text("utf-8").rstrip("\n")
                memory_md.write_text(existing + "\n" + content + "\n", "utf-8")
            else:
                memory_md.write_text(content + "\n", "utf-8")
            await reply.text(f"已记录到 `{workspace}` 下的 CLAUDE.md")
        except Exception as e:
            await reply.text(f"写入失败: {e}")

    # --- /balance [profile]: query API balance for DeepSeek / GLM ---
    if text_lower == "/balance" or text_lower.startswith("/balance "):
        target_profile = text[8:].strip() or None
        from app.balance import get_profile_balance

        await reply.text("🔍 正在查询 API 供应商余额...")
        balance_res = await get_profile_balance(target_profile)
        await reply.view("balance", result=balance_res)
        return

    # --- /notes: write output or text to notes.md ---
    # `/notes last`    → write last execution's final output to notes.md
    # `/notes <task_id>` → write execution output of specific task_id to notes.md
    # `/notes <xxx>`   → write custom text xxx to notes.md
    if (
        text_lower == "/notes"
        or text_lower.startswith("/notes ")
        or text_lower.startswith("/notes:")
        or text_lower.startswith("/notes：")
    ):
        if text_lower.startswith("/notes:") or text_lower.startswith("/notes："):
            raw_arg = text[7:].strip()
        else:
            raw_arg = text[6:].strip()

        clean_arg = raw_arg.lstrip(":：").strip()
        if clean_arg.lower().startswith("task:") or clean_arg.lower().startswith("task_id:"):
            clean_arg = clean_arg.split(":", 1)[1].strip()

        if not clean_arg:
            await reply.text(
                "📝 用法：\n"
                "• `/notes last` — 追加上一次任务执行完成的最终输出到 `notes.md`\n"
                "• `/notes <task_id>` — 追加特定 task_id 的执行完成卡片内容到 `notes.md`\n"
                "• `/notes <内容>` — 将自定义文本追加写入 `notes.md`",
            )
            return

        session = session_manager.get_user_session(skey_for(target))
        workspace = session.workspace if session else settings.get_default_workspace()
        notes_md = Path(workspace) / "notes.md"

        content_to_write = ""
        source_desc = ""

        # 1. Check if 'last'
        if clean_arg.lower() == "last":
            last_res = claude_cli_loop.get_last_result(skey_for(target))
            if not last_res or not last_res.text:
                await reply.text("⚠️ 未找到上一次执行完成的结果记录。")
                return
            content_to_write = last_res.text
            source_desc = f"上一次任务 (`{last_res.task_id}`)"
        else:
            # 2. Check if clean_arg matches a recorded task_id
            task_res = claude_cli_loop.get_task_result(skey_for(target), clean_arg)
            if task_res and task_res.text:
                content_to_write = task_res.text
                source_desc = f"任务 (`{task_res.task_id}`)"
            elif re.match(r"^[a-fA-F0-9]{12}$", clean_arg):
                await reply.text(
                    f"⚠️ 未找到任务 ID 为 `{clean_arg}` 的历史执行记录。\n"
                    f"💡 提示：服务重启或重置会清空内存记录；您可使用 `/notes last` 追加最近一次任务结果。",
                )
                return
            else:
                # 3. Otherwise treat raw_arg as custom text
                content_to_write = raw_arg
                source_desc = "自定义笔记内容"

        try:
            notes_md.parent.mkdir(parents=True, exist_ok=True)
            if notes_md.exists():
                existing = notes_md.read_text("utf-8")
                if existing and not existing.endswith("\n"):
                    existing += "\n"
                new_content = existing + content_to_write + "\n"
            else:
                new_content = content_to_write + "\n"

            notes_md.write_text(new_content, "utf-8")

            preview = content_to_write[:200] + ("..." if len(content_to_write) > 200 else "")
            await reply.text(
                f"✅ 已将{source_desc}追加写入到工作区下的 `notes.md`：\n\n"
                f"📁 文件：`{notes_md}`\n"
                f"📝 内容预览：\n```\n{preview}\n```",
            )
        except Exception as e:
            logger.exception("Failed to write to notes.md")
            await reply.text(f"❌ 写入 `notes.md` 失败：{e}")
        return

    # --- /sh <command>: execute shell command in workspace ---
    if text_lower.startswith("/sh "):
        cmd = text[4:].strip()
        if not cmd:
            await reply.text("用法: `/sh <command>`，例如 `/sh mkdir ZhiWang`")
            return
        session = session_manager.get_user_session(skey_for(target))
        workspace = session.workspace if session else settings.get_default_workspace()
        proc = None
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workspace,
                start_new_session=(sys.platform != "win32"),
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            out = _decode_output(stdout).strip()
            err = _decode_output(stderr).strip()
        except asyncio.TimeoutError:
            if proc is not None:
                _kill_process_tree(proc)
            await reply.text(f"⏱️ 命令超时(30s): `{cmd}`")
            return
        except Exception as e:
            await reply.text(f"执行失败: `{e}`")
            return
        parts = [f"📁 `{workspace}`", f"▶ `{cmd}`"]
        if out:
            display = out if len(out) <= 4000 else out[:3950] + "\n... (输出已截断)"
            parts.append(f"```\n{display}\n```")
        if err:
            display = err if len(err) <= 1000 else err[:950] + "\n..."
            parts.append(f"⚠️ stderr:\n```\n{display}\n```")
        parts.append(f"退出码: {proc.returncode}")
        await reply.text("\n".join(parts))
        return

    # --- Default: run Claude CLI ---
    await _run_claude(text, target)


async def _run_claude(
    prompt: str,
    target: UserTarget,
    session: Session | None = None,
    skip_classify: bool = False,
    resume_session_id: str | None = None,
) -> None:
    """Run Claude CLI with optional model selection card for first message.

    resume_session_id: if set, start claude with ``--resume <id>`` to
    continue a specific session (used by /resume <id>). When None, the
    claude_session_id on the session drives ``--continue``.
    """
    open_id = target.user_id
    chat_id = target.chat_id
    is_group = target.is_group
    audit_logger.log_command_received(open_id, prompt, "started")
    reply = ReplyContext(target)
    try:
        if not session:
            session = session_manager.get_user_session(skey_for(target))
        if not session:
            session = session_manager.create_session(skey_for(target), chat_id)
        elif chat_id and session.chat_id != chat_id:
            # 旧 session 可能带空 chat_id（修复前创建），群消息到达时刷新，
            # 保证后续配置卡片/进度卡片回到群里而不是发进无效私聊
            session.chat_id = chat_id
            session_manager.save_session(session)

        # 自动感知并对齐电脑端最新 CLI 会话（电脑端创建/更新会话向飞书端同步）
        if not resume_session_id and session.workspace:
            try:
                update_info = detect_cli_session_update(session.workspace, session.claude_session_id)
                if update_info.get("has_update"):
                    old_sid = session.claude_session_id
                    new_sid = update_info["latest_session_id"]
                    logger.info("Detected newer CLI session %s (old: %s) for user %s", new_sid, old_sid, open_id)
                    if claude_cli_loop.is_running(skey_for(target)):
                        await claude_cli_loop.cancel_and_wait(skey_for(target))
                    session.claude_session_id = new_sid
                    session.context_tokens = 0
                    session_manager.save_session(session)
                    summary_hint = f"（最新电脑端对话：`{update_info['summary'][:30]}`）" if update_info.get("summary") else ""
                    await reply.text(
                        f"ℹ️ 感知到电脑端本地更新了会话，已自动为您对齐最新会话上下文{summary_hint}。"
                    )
            except Exception as e:
                logger.warning("Failed to check CLI session update: %s", e)

        preferences = preferences_manager.get(open_id)
        profiles = discover_profiles()

        # After a recent /cd into a new workspace, ask whether to reuse last
        # provider/model/mode before running. Only trigger if prefs are complete
        # (otherwise the existing "first-time setup" path below handles it).
        if session.pending_reuse_confirm and preferences.complete:
            session.pending_prompt = prompt
            session_manager.save_session(session)
            profile_label = profiles.get(preferences.model, {}).get("label", preferences.model)
            await reply.view(
                "reuse_last",
                approval_id=uuid.uuid4().hex[:12],
                profile_label=profile_label,
                level=preferences.level,
                mode=preferences.mode,
            )
            return

        # 默认配置继承：未配置时自动使用系统默认值（零门槛冷启动，免去强制三道卡片阻塞）
        default_profile = get_active_profile()
        if default_profile not in profiles and profiles:
            default_profile = next(iter(profiles.keys()))

        applied_defaults = False
        if preferences.model not in profiles and default_profile:
            preferences.model = default_profile
            applied_defaults = True
        if preferences.level not in ("haiku", "sonnet", "opus"):
            preferences.level = getattr(settings, "claude_default_model", "") or "sonnet"
            applied_defaults = True
        if preferences.mode not in ("h", "m", "l"):
            preferences.mode = getattr(settings, "approval_mode", "") or "m"
            applied_defaults = True

        if applied_defaults:
            preferences_manager.save(open_id, preferences)

        need_provider = preferences.model not in profiles
        need_level = preferences.level not in ("haiku", "sonnet", "opus")
        need_mode = preferences.mode not in ("h", "m", "l")

        if need_provider or need_level or need_mode:
            session.pending_prompt = prompt
            session_manager.save_session(session)

            missing_views: list[tuple[str, dict]] = []
            if need_provider:
                missing_views.append(("provider_selection", {
                    "approval_id": uuid.uuid4().hex[:12],
                    "profiles": profiles,
                    "active_profile": "",
                }))
            if need_level:
                missing_views.append(("level_selection", {
                    "approval_id": uuid.uuid4().hex[:12],
                    "current_model": "",
                    "models_map": profile_level_models(preferences.model),
                }))
            if need_mode:
                missing_views.append(("mode_selection", {
                    "approval_id": uuid.uuid4().hex[:12],
                    "active_mode": "",
                }))

            for kind, payload in missing_views:
                await reply.view(kind, **payload)

            prompt_preview = prompt if len(prompt) <= 30 else prompt[:27] + "..."
            await reply.text(
                f"💡 任务已安全暂存：`{prompt_preview}`\n"
                f"系统检测到有 {len(missing_views)} 项初始配置尚未设置。请直接在上方卡片中点选完成，全部设置就绪后系统将全自动重新开始为您执行任务！",
            )
            return

        agent_result = await claude_cli_loop.send_and_wait(
            prompt=prompt,
            open_id=skey_for(target),
            target=target,
            workspace=session.workspace,
            model=preferences.level,
            approval_mode=preferences.mode,
            profile_name=preferences.model,
            claude_session_id=session.claude_session_id or None,
            resume_session_id=resume_session_id,
            effort=preferences.effort,
        )
        # Auto-heal: if process failed because Session ID does not exist on disk
        err_msg = (agent_result.error or "") + (agent_result.text or "")
        if "No conversation found with session ID" in err_msg or "进程通信中断" in err_msg:
            logger.warning("Session ID invalid/lost for user %s, auto-healing...", open_id)
            session.claude_session_id = "__continue__"
            session_manager.save_session(session)
            await reply.text(
                "ℹ️ 检测到历史会话 ID 已在磁盘上失效，已全自动为您重新生成会话并执行任务...",
            )
            await _run_claude(prompt, target, session, skip_classify=True)
            return

        # Process was killed (switch/stop/new) — caller already notified user
        if agent_result.status == "cancelled":
            return

        # Preserve the last known-good ID on transient execution failures.
        # /new and workspace changes are the explicit reset operations.
        if agent_result.session_id:
            session.claude_session_id = agent_result.session_id
            # 微延时确保底层 CLI 进程完成全部流式写盘，避免落盘竞争
            await asyncio.sleep(0.15)
            sync_claude_session_to_cli(session.workspace, agent_result.session_id, prompt)
        if agent_result.input_tokens > 0:
            session.context_tokens = agent_result.input_tokens
        # NOTE: agent_messages intentionally not appended — claude already
        # persists the full conversation in ~/.claude/projects/*/<sid>.jsonl,
        # which _iter_claude_session_messages reads back on demand.

        # Persist session to disk
        session_manager.save_session(session)

        try:
            session.status = TaskStatus(agent_result.status)
        except ValueError:
            session.status = TaskStatus.COMPLETED

        # Result summary is shown on the progress card itself; no extra text message.
        audit_logger.log_command_received(open_id, prompt, agent_result.status)

    except Exception as e:
        logger.exception("Dispatch error for user %s", open_id)
        try:
            await reply.text(
                f"处理指令时出错：{type(e).__name__}: {e}",
            )
        except Exception:
            pass
