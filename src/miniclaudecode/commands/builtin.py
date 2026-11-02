"""Built-in slash commands for MiniClaudeCode."""

from __future__ import annotations

from miniclaudecode.commands.core import Command, CommandContext, CommandInvocation, CommandRegistry, CommandResult, CommandType

_CONFIRMATION_COMMAND_CHOICES = {
    "allow": "allow_once",
    "allow-session": "allow_session",
    "deny": "deny",
}


def create_builtin_command_registry() -> CommandRegistry:
    """Create the default registry used by the TUI."""

    registry = CommandRegistry()
    registry.register(
        Command(
            name="help",
            aliases=("?",),
            description="显示可用命令或单条命令详情。",
            usage="/help [command]",
            type=CommandType.LOCAL,
            argument_hint="[command]",
            handler=_handle_help,
        )
    )
    registry.register(
        Command(
            name="exit",
            aliases=("quit", "q"),
            description="退出 MiniClaudeCode。",
            usage="/exit",
            type=CommandType.UI_STATE,
            handler=lambda ctx, invocation: _call_ui(ctx, "exit_app", 0),
            allowed_while_generating=True,
            allowed_during_confirmation=True,
        )
    )
    registry.register(
        Command(
            name="interrupt",
            aliases=("stop", "cancel"),
            description="中断当前生成。",
            usage="/interrupt",
            type=CommandType.UI_STATE,
            handler=lambda ctx, invocation: _call_ui(ctx, "interrupt"),
            allowed_while_generating=True,
            allowed_during_confirmation=True,
        )
    )
    registry.register(
        Command(
            name="compact",
            description="手动压缩当前上下文。",
            usage="/compact",
            type=CommandType.UI_STATE,
            handler=lambda ctx, invocation: _call_app(ctx, "_handle_compact_command"),
        )
    )
    registry.register(
        Command(
            name="session",
            description="显示当前会话信息。",
            usage="/session",
            type=CommandType.LOCAL,
            handler=lambda ctx, invocation: _call_app(ctx, "_handle_session_command"),
        )
    )
    registry.register(
        Command(
            name="sessions",
            description="列出最近会话。",
            usage="/sessions",
            type=CommandType.LOCAL,
            handler=lambda ctx, invocation: _call_app(ctx, "_handle_sessions_command"),
        )
    )
    registry.register(
        Command(
            name="resume",
            description="恢复指定会话。",
            usage="/resume <session_id>",
            type=CommandType.UI_STATE,
            argument_hint="<session_id>",
            handler=lambda ctx, invocation: _call_app(ctx, "_handle_resume_command", invocation.args),
        )
    )
    registry.register(
        Command(
            name="notes",
            description="列出笔记文件。",
            usage="/notes",
            type=CommandType.LOCAL,
            handler=lambda ctx, invocation: _call_app(ctx, "_handle_notes_command"),
        )
    )
    registry.register(
        Command(
            name="notes-path",
            description="显示用户级和项目级笔记目录。",
            usage="/notes-path",
            type=CommandType.LOCAL,
            handler=lambda ctx, invocation: _call_app(ctx, "_handle_notes_path_command"),
        )
    )
    registry.register(
        Command(
            name="notes-save",
            description="手动触发一次笔记更新。",
            usage="/notes-save",
            type=CommandType.UI_STATE,
            handler=lambda ctx, invocation: _call_app(ctx, "_handle_notes_save_command"),
        )
    )
    registry.register(
        Command(
            name="notes-clear",
            description="显示笔记清空安全提示。",
            usage="/notes-clear",
            type=CommandType.UI_STATE,
            handler=lambda ctx, invocation: CommandResult(
                message="/notes-clear 需要二次确认；本阶段不会静默清空笔记，请手动编辑对应目录。"
            ),
        )
    )
    for name in ("allow", "allow-session", "deny"):
        registry.register(
            Command(
                name=name,
                description="工具确认态兼容命令。",
                usage=f"/{name}",
                type=CommandType.UI_STATE,
                handler=_handle_confirmation_command,
                hidden=True,
                allowed_during_confirmation=True,
            )
        )
    return registry


def _handle_help(ctx: CommandContext, invocation: CommandInvocation) -> CommandResult:
    target = invocation.args.strip().lstrip("/").lower()
    if target:
        command = ctx.registry.get(target)
        if command is None or command.hidden:
            return CommandResult(message=f"未找到可见命令：/{target}。输入 /help 查看可用命令。")
        aliases = ", ".join(f"/{alias}" for alias in command.aliases) or "无"
        type_label = _type_label(command.type)
        return CommandResult(
            message=(
                f"/{command.name}\n"
                f"说明：{command.description}\n"
                f"用法：{command.usage}\n"
                f"类型：{type_label}\n"
                f"别名：{aliases}"
            )
        )

    lines = ["可用命令："]
    for command in ctx.registry.visible_commands():
        lines.append(f"- {command.usage}：{command.description}")
    lines.append("提示：命令大小写不敏感；输入命令前缀后按 Tab 可补全。")
    return CommandResult(message="\n".join(lines))


def _handle_confirmation_command(ctx: CommandContext, invocation: CommandInvocation) -> CommandResult:
    choice = _CONFIRMATION_COMMAND_CHOICES.get(invocation.name)
    if choice is not None and ctx.ui.resolve_confirmation_command(choice):
        return CommandResult()
    return CommandResult(message="当前没有等待确认的工具调用。")


def _call_ui(ctx: CommandContext, method_name: str, *args: object) -> CommandResult:
    method = getattr(ctx.ui, method_name)
    method(*args)
    return CommandResult()


def _call_app(ctx: CommandContext, method_name: str, *args: object) -> CommandResult:
    if ctx.app is None:
        return CommandResult(message="命令运行环境不可用。")
    method = getattr(ctx.app, method_name)
    method(*args)
    return CommandResult()


def _type_label(command_type: CommandType) -> str:
    if command_type is CommandType.LOCAL:
        return "纯本地"
    if command_type is CommandType.UI_STATE:
        return "影响 UI 状态"
    return "Prompt 命令"
