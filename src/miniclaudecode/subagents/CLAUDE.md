# SubAgent 子系统约定

本目录实现 MiniClaudeCode 第十二阶段 SubAgent 系统。

## 模块职责
- `core.py`：定义 AgentDefinition、来源、任务、状态、结果、诊断等基础类型；不做文件 IO，不依赖 Textual。
- `parser.py`：解析 Markdown + YAML frontmatter，返回原始 metadata 和 Markdown body。
- `loader.py`：发现项目级、用户级、内置级和插件级 AgentDefinition 文件。
- `validator.py`：集中校验 AgentDefinition 元数据和正文。
- `registry.py`：保存有效 AgentDefinition，处理覆盖、reload 和 warnings。
- `tools.py`：按全局禁止、自定义限制、后台白名单、`tools` 和 `disallowedTools` 逐层过滤工具。
- `fork.py`：构造 Fork 路径的父历史快照和强约束子任务消息。
- `runtime.py`：创建独立子 Session，复用 AgentRunner 执行 RunToCompletion。
- `tasks.py`：管理后台任务、状态、取消、超时和 task-notification。
- `trace.py`：记录父子链路、任务状态、token 用量和错误摘要。
- `tool.py`：实现暴露给父 Agent 的统一 Agent 工具。
- `commands.py`：注册 `/tasks`、`/task info`、`/task cancel`。

## 边界约定
- 子 Agent 必须使用独立 `Session`，不得复用父 Agent 的 `Session`。
- 子 Agent 的中间消息、工具调用和工具结果不得写入父普通会话历史。
- 父子之间只传最终结果、task id、notification 和 trace 信息。
- Fork 路径只能复制父历史快照，不得持有父 `Session` 引用。
- Fork 路径强约束追加在子任务入口，不重写父上下文前缀。
- 子 Agent 共享基础设施时只能共享无会话状态对象，或显式创建隔离副本。
- Hook 规则可以共享注册表，但 HookEngine 运行时状态不得在父子并发间共享。
- Policy 项目规则可以继承，session policy 必须为子 Agent 新建。
- 工具过滤只能收紧，不能重新放开上层禁止项。
- Agent 工具自身必须全局禁止给子 Agent，防止链式嵌套。
- task-notification 只进入 UI system message，不写普通 session history。

## 安全约定
- 不实现 Worktree 级文件系统隔离。
- 不实现 Agent Team 多 Agent 协作编排。
- 不实现 Trace 跨会话持久化。
- 不修改 `.env`、密钥、CI/CD 配置或 git 历史。
- 不把真实 API key、token、密码写入文档、测试或日志。
