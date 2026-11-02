# teams 子系统约定

本目录实现 MiniClaudeCode 第十四阶段 AgentTeam 团队协作系统。

## 模块职责
- `core.py`：AgentTeam、TeamMember、TeamConfig、TeamMessage、SharedTask 等核心类型。
- `ids.py`：团队名、队员名和 agentID 安全处理。
- `store.py`：团队状态持久化。
- `roster.py`：队员花名册操作。
- `registry.py`：名称注册表，负责 `member name -> agentID -> mailbox file`。
- `tasks.py`：共享任务清单。
- `messages.py`：消息协议辅助构造。
- `mailbox.py`：按 agentID 分文件的 Mailbox。
- `tools.py`：`Task`、`SendMessage`、`TeamCreate`、`TeamDelete` 和工具注入。
- `backends.py`、`detect.py`、`wakeup.py`、`in_process.py`、`tmux.py`、`iterm2.py`：运行后端和唤醒。
- `transcript.py`、`recovery.py`：队员 transcript 持久化和恢复。
- `lifecycle.py`、`spawn.py`、`lead.py`：团队生命周期、队员 spawn 和 Lead 调度。
- `worktrees.py`、`merge.py`、`cleanup.py`：队员 worktree 绑定、收敛合并和清理。
- `coordinator.py`：Coordinator Mode 双锁、工具收窄和四阶段提示词。
- `commands.py`：`/team` slash command。

## 安全边界
- `Task` 和 `SendMessage` 只能注入 AgentTeam 队员工具池或 Coordinator Lead 的收窄工具池，不得进入默认工具集。
- 后端不可用时必须可见失败，不得静默降级。
- Mailbox 必须按 agentID 分文件，队员名称不能直接作为邮箱文件名。
- `TeamDelete` 涉及物理删除文件、目录或工作目录时必须先询问用户确认；默认只归档。
- 收敛合并不得执行 `git push`、`git rebase`、`git reset --hard` 或强制推送。
- 不修改 `.env`、密钥、token、CI/CD 配置或数据库 schema。
- 本目录不实现跨机器分布式 AgentTeam，不实现队员之间实时流式通信。
