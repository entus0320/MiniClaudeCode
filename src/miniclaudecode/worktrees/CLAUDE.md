# Worktrees 子系统约定

本目录实现 Git Worktree 隔离系统。

## 文件职责
- `core.py`：核心 dataclass、enum、错误和诊断类型，不执行 Git 子进程。
- `names.py`：Worktree 名称校验和分支名映射。
- `paths.py`：Worktree 根目录和目标路径解析。
- `git.py`：Git worktree 生命周期命令和状态检查封装。
- `store.py`：状态持久化和 `--resume` 读取。
- `environment.py`：创建后的本地配置复制、hooks 配置、依赖链接和 ignored 文件复制。
- `protection.py`：删除前变更保护检查。
- `cache.py`：工作目录切换时的回调和诊断类型。
- `manager.py`：创建、恢复、进入、退出、删除、状态和列表的统一入口。
- `cleanup.py`：过期临时 worktree 清理。
- `adapter.py`：SubAgent worktree 隔离适配。
- `commands.py`：`/worktree` slash command 注册。

## 安全边界
- 名称和路径必须由 `names.py`、`paths.py` 集中校验。
- 删除必须先经过 `protection.py`。
- Git 检查失败默认视为不安全。
- 自动清理只处理本系统登记且 `temporary=True` 的 worktree。
- 不执行 `git push`、`git rebase`、`git reset --hard` 或强制推送。
- 不复制 `.env`、密钥、token、password 命名文件或 CI/CD 配置。
