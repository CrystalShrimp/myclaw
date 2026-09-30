# MyClaw

本地 Claude Code 的 IM 网关（飞书 + 企业微信双平台长连接）。入口 `app/main.py`，平台适配 `app/feishu/` + `app/wecom/`，共享分发层 `app/dispatch/`，自动化配置 `auto_feishu/` + `auto_wecom/`（Playwright），安装向导 `scripts/setup_wizard.py`。

## Shell working-directory invariant

The shell working directory is NON-PERSISTENT STATE.

Hard rules:

1. NEVER rely on a previous Bash call's `cd`.
2. NEVER use a persistent directory change as part of the workflow.
3. Every Bash command must be independently executable from the repository root.
4. When a command must run inside a subdirectory:
   - Prefer tools with an explicit directory argument:
     - `git -C <dir> ...`
     - `npm --prefix <dir> ...`
   - Otherwise use an explicit subshell:
     - `(cd <dir> && <command>)`
5. NEVER execute `cd <dir>` and expect later tool calls to remain there.
6. NEVER execute `cd <dir> && <command>` directly in the persistent Claude Bash
   shell — wrap it in a subshell.
7. Hook/script paths MUST NOT depend on the current working directory.
8. A shell command changing cwd must never affect subsequent Edit, Write,
   Read, Grep, Glob, hook, or Bash operations.

Known incident: persistent `cd launcher_windows` made the relative-path hook
(`python scripts/hooks/pre_tool_use.py`) unresolvable and blocked ALL tools.
Hooks now cd to `${CLAUDE_PROJECT_DIR}` first; keep that pattern.
