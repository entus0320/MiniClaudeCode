# skills 目录约定

本目录实现 MiniClaudeCode 的 Skill 系统。

## 文件职责
- `core.py`：Skill 数据模型、枚举和异常；不得做文件 IO，不依赖 Textual。
- `loader.py`：发现项目、用户、内置三层 Skill，解析 Markdown frontmatter 和目录型资源。
- `validator.py`：校验 Skill 名称、模式、上下文策略、工具白名单和目录型工具资源。
- `registry.py`：维护当前可用 Skill、覆盖关系、警告和重扫结果。
- `runtime.py`：维护已激活 Skill，生成环境上下文动态指令，过滤模型可见工具。
- `commands.py`：把 Skill 暴露为 slash command 和管理命令。
- `directory_tools.py`：把目录型 Skill 的工具 schema 和实现脚本适配成标准 Tool。
- `__init__.py`：导出稳定公共类型。

## 设计约定
- Skill 名称必须能安全映射为 slash command，禁止空白、`/`、`\\`。
- 文件型 Skill 使用 YAML frontmatter + Markdown 正文。
- 目录型 Skill 优先使用 `skill.md` 作为入口文件，工具资源放在 `tools/`。
- 单个 Skill 解析失败时跳过并记录警告；白名单缺失工具、命令冲突和目录工具安全错误必须 fail-fast。
- 同名 Skill 覆盖优先级：项目目录 > 用户目录 > 内置 Skill。
- Skill 摘要和激活 SOP 只进入环境上下文，不写入普通消息历史。
- `mode: isolated` 本阶段只预留和提示，不实现真实独立子会话。
- 多个 active Skill 的工具白名单取并集。

## 安全约定
- 目录型 Skill 工具必须适配为标准 `Tool`，执行前继续经过 policy 和 confirmation。
- 目录工具脚本路径必须位于 Skill 包目录内，不得通过符号链接或相对路径跳出。
- 目录工具脚本只接收 JSON arguments，不接收完整会话历史或 secrets。
- 不在 Skill 命令中静默删除、清空或覆盖用户文件。
- 内置 commit/review/test Skill 不得自动执行 `git push`、强制重写历史或发布动作。
