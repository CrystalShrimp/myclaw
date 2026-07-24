from __future__ import annotations

import json


# ===== Model Selection Card (manual selection, before execution) =====


def build_model_selection_card(
    approval_id: str = "",
    prompt: str = "",
    default_model: str = "sonnet",
    current_model: str = "",
) -> dict:
    """模型规格选择卡片 — 让用户选择 haiku / sonnet / opus。"""
    active = current_model or default_model or "sonnet"
    model_desc = {"haiku": "Haiku (轻量/快速)", "sonnet": "Sonnet (标准/推荐)", "opus": "Opus (旗舰/最强)"}
    all_models = [("haiku", "secondary"), ("sonnet", "primary"), ("opus", "danger")]

    actions = []
    for model_code, btn_type in all_models:
        is_active = (model_code == active)
        actions.append({
            "tag": "button",
            "text": {
                "tag": "plain_text",
                "content": f"{'✓ ' if is_active else ''}{model_desc[model_code]}"
            },
            "type": btn_type if not is_active else "default",
            "value": {
                "approval_id": approval_id,
                "act": "switch_model",
                "model": model_code,
                "type": "model_selection",
            },
        })

    display_prompt = f"**当前已选：** `{active}`\n"
    if prompt:
        display_prompt += f"**暂存指令：** {prompt[:200]}"

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "选择模型规格 (Model Level)"},
            "template": "blue",
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": display_prompt,
                },
            },
            {"tag": "hr"},
            {
                "tag": "action",
                "actions": actions,
            },
        ],
    }


# ===== Profile Selection Card (switch provider: GLM, Kimi, etc.) =====


def build_profile_selection_card(
    approval_id: str,
    profiles: dict[str, dict],
    active_profile: str,
) -> dict:
    """Profile 切换卡片 — 让用户选择不同的模型供应商。"""
    profile_lines = []
    for name, info in profiles.items():
        marker = " ← 当前" if name == active_profile else ""
        label = info.get("label", name)
        model = info.get("model", "")
        profile_lines.append(f"- **{label}** ({name}) — 模型: {model}{marker}")

    profile_text = "\n".join(profile_lines) if profile_lines else "未发现任何 profile 配置"

    buttons = []
    for name, info in profiles.items():
        label = info.get("label", name)
        buttons.append({
            "tag": "button",
            "text": {"tag": "plain_text", "content": f"切换到 {label}"},
            "type": "default",
            "value": {
                "approval_id": approval_id,
                "act": "switch_profile",
                "profile": name,
                "type": "profile_switch",
            },
        })

    if not buttons:
        buttons.append({
            "tag": "button",
            "text": {"tag": "plain_text", "content": "关闭"},
            "type": "default",
            "value": {"approval_id": approval_id, "act": "close", "type": "profile_switch"},
        })

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "切换模型供应商 (Provider)"},
            "template": "purple",
        },
        "elements": [
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": profile_text},
            },
            {"tag": "hr"},
            {"tag": "action", "actions": buttons},
        ],
    }


# ===== Tool Approval Card (used by PreToolUse hook) =====


def build_tool_approval_card(
    approval_id: str,
    tool_name: str,
    tool_input: dict,
    session_id: str = "",
) -> dict:
    """工具执行确认卡片 — PreToolUse hook 审批用。"""
    # Format input for display
    input_display = json.dumps(tool_input, ensure_ascii=False, indent=2)
    if len(input_display) > 1000:
        input_display = input_display[:1000] + "\n..."

    # Risk-based color
    high_risk_tools = {"Bash", "Write", "Edit", "NotebookEdit"}
    is_high = tool_name in high_risk_tools
    color = "red" if is_high else "orange"
    risk_label = "高风险" if is_high else "低风险"

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "工具执行确认"},
            "template": color,
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": (
                        f"**工具：** {tool_name}\n"
                        f"**风险：** {risk_label}\n"
                        f"**参数：**\n\n{input_display}\n"
                    ),
                },
            },
            {"tag": "hr"},
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "允许执行"},
                        "type": "primary",
                        "value": {
                            "approval_id": approval_id,
                            "act": "approve",
                            "type": "tool_execution",
                        },
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "拒绝执行"},
                        "type": "danger",
                        "value": {
                            "approval_id": approval_id,
                            "act": "reject",
                            "type": "tool_execution",
                        },
                    },
                ],
            },
        ],
    }


# ===== Streaming Card (real-time output updates) =====


def build_streaming_card(
    model: str,
    accumulated_text: str,
    finished: bool = False,
    continued: bool = False,
    session_id: str = "",
) -> dict:
    """流式输出卡片 — Claude CLI 思考/输出实时更新。"""
    if finished:
        status = "执行完成"
        color = "green"
    elif continued:
        status = "▶ 输出继续..."
        color = "turquoise"
    else:
        status = "思考中..."
        color = "blue"
    sid_hint = f" [{session_id[:8]}]" if session_id else ""

    display_text = accumulated_text[:3500] if len(accumulated_text) > 3500 else accumulated_text
    trunc = f"\n\n... (truncated, total {len(accumulated_text)} chars)" if len(accumulated_text) > 3500 else ""

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"[{model}] {status}{sid_hint}"},
            "template": color,
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": display_text + trunc,
                },
            },
        ],
    }


# ===== Final Result Card =====


def build_tool_result_card(
    task_id: str,
    result_text: str,
    status: str,
    cost_usd: float = 0,
    duration_s: float = 0,
    tools_count: int = 0,
    error: str = "",
) -> dict:
    """最终结果卡片 — Claude CLI 执行完成后显示。"""
    status_label = {
        "completed": "执行完成",
        "failed": "执行失败",
        "cancelled": "已取消",
    }.get(status, status)

    color = "green" if status == "completed" else "red"

    stats = (
        f"**任务：** {task_id}\n"
        f"**耗时：** {duration_s:.1f}s\n"
        f"**工具调用：** {tools_count} 次\n"
        f"**费用：** ${cost_usd:.4f}"
    )
    if error:
        stats += f"\n**错误：** {error[:500]}"

    display = result_text[:3500] if len(result_text) > 3500 else result_text
    trunc = f"\n\n... (truncated, total {len(result_text)} chars)" if len(result_text) > 3500 else ""

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": status_label},
            "template": color,
        },
        "elements": [
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": stats},
            },
            {"tag": "hr"},
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": f"**结果：**\n{display}{trunc}"},
            },
        ],
    }


# ===== Error Card =====


def build_error_card(title: str, detail: str) -> dict:
    """错误卡片。"""
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": "red",
        },
        "elements": [
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": detail[:3500]},
            },
        ],
    }


# ===== Simple Text Card =====


def build_simple_text_card(title: str, content: str, color: str = "blue") -> dict:
    """Build a simple text notification card."""
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": color,
        },
        "elements": [
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": content},
            },
        ],
    }


def build_cd_selection_card(
    current_workspace: str,
    projects: list[str],
) -> dict:
    """构建 cd 选择卡片，展示已有的项目列表（按 LRU 排序，只展示文件夹名）以及手动路径输入。"""
    from pathlib import Path
    
    options = []
    # 限制下拉列表最多 90 个，保留一些空间以防超出飞书卡片总长度或选项数上限
    for p in projects[:90]:
        p_path = Path(p)
        label = str(p_path)  # Show the full path to disambiguate same-named projects.
        if len(label) > 100:
            label = "..." + label[-97:]
        options.append({
            "text": {
                "tag": "plain_text",
                "content": label
            },
            "value": p
        })

    elements = [
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"📁 **当前工作区：** {current_workspace}"
            }
        }
    ]

    if options:
        elements.extend([
            {"tag": "hr"},
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": "✨ **选择已有项目工作区：**\n在下拉菜单中选择一个电脑上已有的 Claude Code 项目目录。"
                }
            },
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "select_static",
                        "placeholder": {
                            "tag": "plain_text",
                            "content": "点击选择已有的项目目录..."
                        },
                        "value": {
                            "type": "workspace_select",
                            "act": "pre_switch"
                        },
                        "options": options
                    }
                ]
            }
        ])

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "选择工作区"},
            "template": "blue",
        },
        "elements": elements
    }


def build_claude_dir_selection_card(detected_path: str) -> dict:
    """Build the first-run card used to locate Claude's data directory."""
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "设置 Claude 数据目录"},
            "template": "blue",
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": (
                        "myclaw 需要读取 Claude 的 history.jsonl 和 projects 目录，"
                        "用于列出曾经启动过 Claude session 的项目。"
                    ),
                },
            },
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": f"使用检测目录：{detected_path}"},
                        "type": "primary",
                        "value": {
                            "type": "claude_dir_select",
                            "act": "set_detected",
                            "path": detected_path,
                        },
                    }
                ],
            },
            {"tag": "hr"},
            {
                "tag": "input",
                "name": "claude_data_dir",
                "placeholder": {
                    "tag": "plain_text",
                    "content": "输入 .claude 文件夹的绝对路径",
                },
                "value": {"type": "claude_dir_select", "act": "set_manual"},
            },
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "保存目录"},
                        "value": {"type": "claude_dir_select", "act": "set_manual"},
                    }
                ],
            },
        ],
    }

def build_cd_confirm_card(
    target_path: str,
    is_new: bool,
    git_branch: str,
    claude_md: str,
    warning_running: bool = False,
) -> dict:
    """构建 cd 确认切换卡片，展示目标项目详情、关联状态以及可能的中断警告。"""
    type_label = "🆕 新建目录" if is_new else "📁 已有目录"
    
    details = (
        f"📍 **目标路径：** {target_path}\n"
        f"🏷️ **属性：** {type_label}\n"
        f"🌿 **Git 分支：** {git_branch}\n"
        f"📝 **CLAUDE.md：** {claude_md}"
    )
    
    elements = [
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": details
            }
        }
    ]
    
    if warning_running:
        elements.extend([
            {"tag": "hr"},
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": "⚠️ **注意：当前正有运行中的 Claude Code 任务，确认切换工作区将会强行中断当前任务！**"
                }
            }
        ])
        
    elements.extend([
        {"tag": "hr"},
        {
            "tag": "action",
            "actions": [
                {
                    "tag": "button",
                    "text": {
                        "tag": "plain_text",
                        "content": "确认切换"
                    },
                    "type": "primary",
                    "value": {
                        "type": "workspace_select",
                        "act": "confirm_switch",
                        "path": target_path
                    }
                },
                {
                    "tag": "button",
                    "text": {
                        "tag": "plain_text",
                        "content": "返回选择"
                    },
                    "type": "default",
                    "value": {
                        "type": "workspace_select",
                        "act": "cancel_switch"
                    }
                }
            ]
        }
    ])
    
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "确认切换工作区"},
            "template": "orange" if warning_running else "blue",
        },
        "elements": elements
    }


def build_file_selection_card(
    workspace: str,
    files: list[dict],
) -> dict:
    """构建文件选择卡片，类似于 /cd 卡片，展示工作区路径下的文件列表供用户发送。"""
    options = []
    for item in files[:90]:
        rel_path = item.get("rel_path", "")
        size_str = item.get("size_str", "")
        abs_path = item.get("abs_path", "")
        label = f"{rel_path} ({size_str})"
        if len(label) > 100:
            label = "..." + label[-97:]
        options.append({
            "text": {
                "tag": "plain_text",
                "content": label
            },
            "value": abs_path
        })

    elements = [
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"📁 **当前工作区：** `{workspace}`"
            }
        }
    ]

    if options:
        elements.extend([
            {"tag": "hr"},
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"📄 **可发送的文件列表 (共发现 {len(files)} 个文件)：**\n请在下拉菜单中选择您要推送到飞书的文件："
                }
            },
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "select_static",
                        "placeholder": {
                            "tag": "plain_text",
                            "content": "点击选择文件进行发送..."
                        },
                        "value": {
                            "type": "file_send_select",
                            "act": "send_file"
                        },
                        "options": options
                    }
                ]
            }
        ])
    else:
        elements.extend([
            {"tag": "hr"},
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": "⚠️ **未找到可发送的文件。**\n当前工作区目录下暂无可供发送的普通文件。"
                }
            }
        ])

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "选择工作区文件发送"},
            "template": "blue",
        },
        "elements": elements
    }


def build_help_card() -> dict:
    """构建 /help 指令手册交互卡片，采用 lark_md 渲染高亮且精美的指令菜单。"""
    help_md = (
        "🤖 **MyClaw 飞书机器人指令手册**\n\n"
        "**⚙️ 厂家与模型设置**\n"
        "- `/provider [profile]` : 切换模型供应商/厂家 (如 zhipu, deepseek, anthropic)\n"
        "- `/model [haiku|sonnet|opus]` : 切换模型规格/等级 (同 `/level`)\n"
        "- `/mode [h|m|l]` : 切换审批模式 (`h`高容忍 / `m`中风险审批 / `l`低容忍全审批)\n\n"
        "**📁 工作区与文件管理**\n"
        "- `/pwd` : 查看当前关联的项目工作区绝对路径\n"
        "- `/cd [path]` : 切换或新建工作区 (不带路径则弹出交互选择卡片)\n"
        "- `/show` : 选择并直接将工作区下的文件发送到飞书\n\n"
        "**💬 会话与控制**\n"
        "- `/status` : 查看当前会话详情与上下文用量\n"
        "- `/new` : 重置并开启全新会话 (保留当前工作区)\n"
        "- `/stop` (或 `停止`) : 强制中断当前正在运行的任务\n"
        "- `/continue [prompt]` : 恢复并继续上次的对话\n"
        "- `/resume <session_id>` : 恢复指定的历史 Session\n"
        "- `/compact` : 压缩当前会话上下文\n"
        "- `/clean` : 清理旧会话数据\n\n"
        "**🛠️ 实用工具**\n"
        "- `/mem <内容>` : 记录备忘信息到当前工作区的 `CLAUDE.md`\n"
        "- `/sh <command>` : 在当前工作区执行一条终端命令 (30秒超时)\n"
        "- `/help` (或 `帮助`) : 显示本帮助手册"
    )
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "MyClaw 指令手册"},
            "template": "purple",
        },
        "elements": [
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": help_md},
            }
        ],
    }


def build_mode_selection_card(approval_id: str = "", active_mode: str = "") -> dict:
    """Build a selection card for Approval Mode (h: High Tolerance / Auto, m: Medium / Balanced, l: Low Tolerance / Strict)."""
    options = [
        ("h", "⚡ 全自动模式 (h)", "高容忍放行，全自动运行", "success"),
        ("m", "⚖️ 平衡模式 (m)", "只读自动放行，敏感写操作需确认", "primary"),
        ("l", "🛡️ 严格模式 (l)", "低容忍拦截，所有敏感/操作需确认", "warning"),
    ]
    actions = []
    for mode_code, title, desc, btn_type in options:
        is_active = (mode_code == active_mode)
        actions.append({
            "tag": "button",
            "text": {
                "tag": "plain_text",
                "content": f"{'✓ ' if is_active else ''}{title}"
            },
            "type": btn_type if not is_active else "default",
            "value": {
                "type": "mode_switch",
                "act": "switch_mode",
                "mode": mode_code,
                "approval_id": approval_id,
            }
        })

    elements = [
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": "请选择 **工具执行审批模式 (Mode)**：\n- **⚡ 全自动模式 (h)**：高容忍静默运行，无需打扰。\n- **⚖️ 平衡模式 (m)**：只读自动放行，写操作需审批。\n- **🛡️ 严格模式 (l)**：低容忍严格把关，需人工确认。"
            }
        },
        {"tag": "hr"},
        {
            "tag": "action",
            "actions": actions
        }
    ]
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "选择审批模式 (Mode)"},
            "template": "orange",
        },
        "elements": elements
    }


