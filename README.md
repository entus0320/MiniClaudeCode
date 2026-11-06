# MiniClaudeCode

使用 Python 构建的终端编程 Agent Runtime，围绕“模型提出工具调用、运行时校验并执行、结果回填后继续推理”组织代码任务。支持文件检索与修改、命令执行、多模型接入，以及会话和上下文管理。

项目用于 Agent 工程学习与实践。功能说明以仓库代码为依据，不代表已通过生产环境验证。

## 核心能力

- **模型与交互**：通过 Provider 抽象接入 Anthropic、OpenAI 与 OpenAI 兼容接口；使用 Textual 展示流式文本、工具调用和确认提示。
- **有限 Agent 循环**：支持多轮工具调用、结果回填、取消与失败反馈；默认轮数预算为 20，达到上限后进行无工具总结。
- **工具系统**：统一工具注册、参数处理与执行结果；允许符合条件的只读工具分组并发，其他工具按执行顺序处理。
- **MCP Client**：支持 `stdio` 与 `streamable_http`，完成初始化、工具发现和调用，复用连接并进行有界重连。
- **上下文管理**：按预算将超长工具结果落盘并保留预览，支持自动历史压缩与手动 `/compact`。
- **会话与记忆**：追加式 JSONL 会话持久化与恢复；通过用户级、项目级笔记保存跨会话信息。
- **扩展机制**：包含指令文件加载、Skills、Hooks、SubAgent、后台任务、Agent Teams 和 Git Worktree 管理模块。部分功能需要配置及外部环境支持。

## 运行流程

```text
用户输入 → Session 保存消息 → Provider 调用模型
                                  │
                      ┌───────────┴───────────┐
                      │                       │
                   文本回答                工具调用请求
                      │                       │
                    结束              权限与参数检查
                                              │
                                           执行工具
                                              │
                                 调用及结果写入 Session
                                              │
                                       进入下一轮模型调用
```

核心实现见 [AgentRunner](src/miniclaudecode/agent.py)、[Session](src/miniclaudecode/session.py) 和 [Provider 接口](src/miniclaudecode/providers/base.py)。

## 快速开始

需要 Python 3.11+。使用 Worktree 功能时还需要 Git 和可用的 Git 工作仓库。

### 1. 获取并安装

```bash
git clone https://github.com/entus0320/MiniClaudeCode.git
cd MiniClaudeCode
python -m venv .venv
```

激活虚拟环境：

```powershell
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
```

```bash
# macOS / Linux
source .venv/bin/activate
```

安装项目：

```bash
python -m pip install -e .
```

### 2. 创建本地配置

Windows PowerShell：

```powershell
Copy-Item config.example.yaml config.yaml
Copy-Item .env.example .env
```

macOS / Linux：

```bash
cp config.example.yaml config.yaml
cp .env.example .env
```

在 `config.yaml` 中选择 `active_provider`，并把对应 Provider 的 `model` 改为账号实际可用的模型。示例模型名称不保证对所有服务可用。

在本地 `.env` 中填写所选服务的凭据：

```dotenv
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
```

使用 OpenAI 兼容服务时，选择 `active_provider: openai-compatible`，配置其 `base_url` 和 `model`，并设置 `OPENAI_API_KEY`。服务需要支持流式 Chat Completions；工具模式还要求支持工具调用及流式工具参数。

完整配置见 [config.example.yaml](config.example.yaml)。实际凭据、`config.yaml` 和运行状态文件应保留在本地。

### 3. 启动

在已配置的项目目录运行：

```bash
miniclaudecode
```

示例任务：

- “读取 README.md，概括项目结构。”
- “搜索 AgentRunner 的定义，解释一次工具调用的执行流程。”

文件工具以启动时的工作目录为访问边界。写入、修改和命令执行在默认策略下需要确认；取消属于尽力中断，不保证撤销已经发生的操作。

## 内置工具

| 工具 | 用途 |
| --- | --- |
| `read_file` | 读取文本文件 |
| `glob_files` | 按模式查找文件 |
| `grep_content` | 按正则表达式搜索内容 |
| `write_file` | 写入文件 |
| `edit_file` | 精确替换文件中的指定文本 |
| `run_command` | 执行命令并收集输出、退出码和超时信息 |

实现见 [tools/](src/miniclaudecode/tools/)。

## 常用命令

| 命令 | 用途 |
| --- | --- |
| `/help` | 查看命令及用法 |
| `/interrupt` | 请求中断当前生成 |
| `/compact` | 手动压缩上下文 |
| `/session`、`/sessions` | 查看当前及近期会话 |
| `/resume <session_id>` | 恢复指定会话 |
| `/notes`、`/notes-path`、`/notes-save` | 查看及保存记忆笔记 |
| `/exit` | 退出 |

命令注册表提供帮助和补全信息；条件启用的模块可能注册额外命令，以运行时 `/help` 为准。

## MCP、SubAgent 与 Worktree

MCP 配置示例位于 `config.example.yaml` 的 `mcp_servers` 部分。敏感环境变量和请求头可通过 `env_from`、`headers_env` 引用。发现的工具使用服务器名前缀注册，并纳入权限检查。

`safe_tools` 用于标识可参与安全并发或断线重试的工具，不等于授予执行权限。对未标记为安全的在途调用，不在断线后自动重放，以避免重复副作用。

SubAgent 通过独立 Session、工具过滤及任务状态管理执行子任务。Worktree 模块可为相关任务提供独立 Git 工作目录；团队模块包含进程内、tmux 和 iTerm2 后端，其可用性依赖平台和配置。实现分别见 [subagents/](src/miniclaudecode/subagents/)、[worktrees/](src/miniclaudecode/worktrees/) 和 [teams/](src/miniclaudecode/teams/)。

## 权限与限制

策略引擎提供 `allow / ask / deny`，支持项目策略和会话内临时授权，策略示例见 [miniclaudecode.policy.example.yaml](miniclaudecode.policy.example.yaml)。

代码包含文件路径边界检查、敏感文件保护与危险命令检测。这些属于应用层限制，**不是操作系统级沙箱，也不保证拦截所有危险命令或提示词注入**。执行命令时仍使用本机权限；Git Worktree 也不是进程或网络安全隔离。

当前边界还包括：

- 有限轮数工具循环，不保证任意长任务自动完成。
- MCP 主要覆盖工具发现与调用，不宣称完整协议或 OAuth 支持。
- 没有代码库语义索引或 RAG。
- 记忆和摘要由模型生成，可能遗漏或错误概括信息。
- 仓库包含相关模块及测试，不代表所有平台、后端或真实 API 组合均已验证。

## 测试

安装项目依赖后，可使用标准库 unittest 运行：

```bash
# 单元测试
python -m unittest discover -s tests -p "test_*.py"

# 单独发现 tests/e2e 下的模拟流程测试
python -m unittest discover -s tests/e2e -p "test_*.py"
```

[tests/](tests/) 包含模型适配、工具执行、权限、上下文、会话、MCP、SubAgent、团队及 Worktree 等测试。模拟流程测试使用脚本化 Provider，不能替代真实模型、外部 MCP 服务和平台兼容性验收。本文不提供未经实际运行核验的测试通过率或覆盖率。

## 代码导航

| 路径 | 职责 |
| --- | --- |
| [cli.py](src/miniclaudecode/cli.py) / [tui.py](src/miniclaudecode/tui.py) | 启动装配与终端交互 |
| [agent.py](src/miniclaudecode/agent.py) | Agent 循环与事件流 |
| [providers/](src/miniclaudecode/providers/) | 模型适配 |
| [tools/](src/miniclaudecode/tools/) / [policy.py](src/miniclaudecode/policy.py) | 工具与权限 |
| [context.py](src/miniclaudecode/context.py) | 结果卸载与上下文压缩 |
| [session.py](src/miniclaudecode/session.py) / [session_store.py](src/miniclaudecode/session_store.py) | 消息历史与持久化 |
| [memory.py](src/miniclaudecode/memory.py) | 记忆笔记 |
| [mcp/](src/miniclaudecode/mcp/) | MCP 连接与工具适配 |
| [skills/](src/miniclaudecode/skills/) / [hooks/](src/miniclaudecode/hooks/) | 扩展加载与生命周期事件 |
