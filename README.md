# MyClaw

把本地 Claude Code 接入飞书 / 企业微信的协作控制层。用户在 IM 中发起任务、查看进度、审批风险操作，任务实际仍由本机已有的 Claude Code 执行。MyClaw 负责消息接入、任务调度、配置管理、权限审批和结果展示。

## 核心能力

- **飞书 / 企业微信双平台同时在线**：同一进程两条长连接；`windows_launch/setup.cmd` / `macos_launch/setup.sh` 选择平台，`MYCLAW_CHANNELS` 留空时按凭据自动探测，会话按平台隔离互不干扰；
- **IM 发起任务，进度实时回流**：飞书同一张卡片增量刷新工具调用、阶段性输出和 token 用量，任务完成后切为最终结果；企业微信用流式消息刷新（超 10 分钟自动换流续传）；
- **高风险操作审批卡片**：写文件、删文件、推代码等高风险操作弹审批卡片，决策、操作人和时间写入 `logs/audit.log`；
- **终端与 IM 双向会话延续**：本机终端和 IM 共享 Claude Code 原生会话，离开工位在 IM 接手，回到工位在终端继续，上下文不丢失；
- **三档审批模式 + 项目级自定义规则**：`h` 严格 / `m` 平衡 / `l` 自动；`m` 模式下的审批边界可在 `config/approval_rules.json` 自定义；
- **多用户多工作区隔离**：每个用户分别维护自己的工作区、模型供应商、档位、审批模式和会话；
- **多人多 profile 模型供应商**：通过 `config/settings_<name>.json` 配置 GLM、Kimi、DeepSeek 等，IM 里 `/provider` 即时切换。

## 快速开始

1. **首次配置消息平台**：双击 `windows_launch\setup.cmd` 选择平台——`1` 飞书（Playwright 全自动配置，详见 [auto_feishu/README.md](auto_feishu/README.md)）/ `2` 企业微信（手动向导 + 长连接实测）/ `3` 两者都配；
2. **启动服务**：双击 `windows_launch\MyClaw.bat`（macOS 用 `macos_launch/MyClaw.command`），任务栏出现托盘图标即表示启动成功。启动脚本会先清理可能存在的残留进程（防止"两个托盘抢互斥锁导致图标不显示"的问题）；
3. **在 IM 中发消息**给机器人，按引导完成模型供应商、档位和审批模式配置后即可使用。

代码或配置更新后，使用 `windows_launch\MyClaw-Restart.bat` 重启，比手动结束后台进程更稳妥。健康检查地址：`http://127.0.0.1:8080/health`。

## 主要指令（IM 中发送）

| 分类 | 指令 | 用途 |
|---|---|---|
| 工作区 | `/cd`、`/pwd`、`/file` | 切换/查看工作区，发送工作区文件 |
| 会话 | `/new`、`/stop`、`/continue`、`/resume <id>`、`/session`、`/compact`、`/clean` | 创建/中断/继续/恢复/列出选择/压缩/清理会话 |
| 配置 | `/provider`、`/model`、`/mode`、`/reset` | 模型供应商、档位、审批模式 |
| 工具 | `/status`、`/mem`、`/notes`、`/sh`、`/help` | 状态、记忆、笔记、Shell、帮助 |

完整指令说明见 [doc/input.md 附录 A](doc/input.md)。

## 文档导航

| 文档 | 面向读者 | 内容 |
|---|---|---|
| [安装步骤.md](安装步骤.md) | 客户 | 四步安装与常见问题 |
| [doc/input.md](doc/input.md) | 产品 / 研究 | 项目定位、核心能力、使用流程、安全治理、附录（指令、启动参数、部署配置） |
| [doc/CLAUDEME.md](doc/CLAUDEME.md) | 部署 Agent | 10 分钟内完成单机部署的工程指南 |
| [doc/setup.md](doc/setup.md) | 部署 / 研究 | 配置与部署规范（三大全局配置要素、Setup 菜单设计） |
| [auto_feishu/README.md](auto_feishu/README.md) | 客户 | 飞书一键配置脚本的使用与失败恢复 |

## 目录结构

```
app/            后端 FastAPI 服务（事件分发、卡片、审批、Claude Code 调度）
auto_feishu/    飞书一键配置子模块（TypeScript + Playwright）
config/         供应商 profile 与模板、审批规则、Claude Code 子进程设置
doc/            产品文档
logs/           运行日志（myclaw.log、audit.log）和会话状态
scripts/        托盘程序、重启脚本、开机自启等
windows_launch/ Windows 客户入口（安装/配置/启动/重启）
macos_launch/   macOS 客户入口
```

## 配置入口

- **`.env`**：从 `config/env.example` 复制。包含飞书凭据（`FEISHU_*`）、企业微信凭据（`WECOM_BOT_ID`/`WECOM_SECRET`，长连接模式）、Claude CLI 路径、`ALLOWED_USERS` 访问白名单（飞书 ID 直接填，企微 userid 加 `wecom:` 前缀）、审批模式与超时、服务端口等；
- **`config/settings_<name>.json`**：模型供应商 profile，参考 `config/settings_<name>.example.json`；当前生效项由 `config/active_profile` 指定；
- **`config/approval_rules.json`**：平衡模式（`m`）下的审批规则，缺失或非法时回退到 `app/hooks/router.py` 内置规则；
- **`icon.png` / `icon.jpg` / `icon.ico`**（可选）：放到项目根目录可自定义托盘图标，缺失时使用系统默认图标。

各项细节见 [doc/input.md 附录](doc/input.md)。
