# commands 目录约定

本目录实现 MiniClaudeCode 的集中式命令系统。

## 文件职责
- `core.py`：命令类型、命令定义、解析器、注册表、补全项和 UI 协议；不得依赖 Textual。
- `builtin.py`：注册内置命令和轻量 handler wrapper；可以通过命令上下文调用 TUI 适配方法，但不直接操作 Textual widget。
- `__init__.py`：导出稳定公共类型。

## 设计约定
- 命令名和别名注册时统一按大小写不敏感处理。
- 隐藏命令参与冲突检测，但不参与帮助和补全。
- `CommandType.PROMPT` 是预留类型，本阶段不得直接把 prompt 命令送入对话流。
- 命令核心模块只描述命令行为，不管理 TUI 布局。
- 需要 UI 能力时通过 `CommandUI` 协议调用。

## 安全约定
- 不在命令 handler 中静默删除或清空文件。
- `/notes-clear` 等破坏性操作必须有显式确认流程；本阶段只提示，不执行。
- 命令错误应转为用户可见消息，不应让 TUI 崩溃。
