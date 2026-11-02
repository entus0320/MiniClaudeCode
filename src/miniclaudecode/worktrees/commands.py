"""Slash commands for managed Git worktrees."""

from __future__ import annotations

from miniclaudecode.commands.core import Command, CommandInvocation, CommandRegistry, CommandResult, CommandType
from miniclaudecode.worktrees.manager import WorktreeManager


def register_worktree_commands(command_registry: CommandRegistry, manager: WorktreeManager) -> None:
    """Register /worktree lifecycle commands."""

    command_registry.register(
        Command(
            name="worktree",
            description="管理 Git worktree 隔离目录。",
            usage="/worktree <list|create|enter|exit|remove|status> [name]",
            type=CommandType.LOCAL,
            argument_hint="<list|create|enter|exit|remove|status> [name]",
            handler=lambda ctx, invocation: _handle_worktree(manager, invocation),
            allowed_during_confirmation=False,
        )
    )


def _handle_worktree(manager: WorktreeManager, invocation: CommandInvocation) -> CommandResult:
    parts = invocation.args.split()
    if not parts:
        return CommandResult(message="用法：/worktree <list|create|enter|exit|remove|status> [name]")
    action = parts[0]
    if action == "list":
        records = manager.list_records()
        if not records:
            return CommandResult(message="暂无托管 Worktree。")
        lines = ["托管 Worktree："]
        for record in records:
            active = " *active*" if manager.active_name == record.name else ""
            lines.append(f"- {record.name}{active} | {record.status.value} | {record.branch} | {record.path}")
        return CommandResult(message="\n".join(lines))
    if action == "status":
        return CommandResult(message=manager.status_text(parts[1] if len(parts) > 1 else None))
    if action == "exit":
        return CommandResult(message=manager.exit().message)
    if action in {"create", "enter", "remove", "delete"} and len(parts) < 2:
        return CommandResult(message=f"用法：/worktree {action} <name>")
    if action == "create":
        result = manager.create(parts[1])
        return CommandResult(message=result.message)
    if action == "enter":
        result = manager.enter(parts[1])
        return CommandResult(message=result.message)
    if action in {"remove", "delete"}:
        confirm = "--confirm-discard" in parts[2:]
        result = manager.remove(parts[1], confirm_discard=confirm)
        prefix = "提示：delete 是兼容别名，推荐使用 /worktree remove。\n" if action == "delete" else ""
        return CommandResult(message=prefix + result.message)
    return CommandResult(message="未知 worktree 子命令。用法：/worktree <list|create|enter|exit|remove|status> [name]")
