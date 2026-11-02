"""Slash commands for SubAgent background tasks."""

from __future__ import annotations

from miniclaudecode.commands.core import Command, CommandContext, CommandInvocation, CommandRegistry, CommandResult, CommandType
from miniclaudecode.subagents.tasks import TaskManager
from miniclaudecode.subagents.trace import TraceRegistry


def register_subagent_commands(command_registry: CommandRegistry, task_manager: TaskManager, trace_registry: TraceRegistry | None = None) -> None:
    """Register /tasks, /task info, and /task cancel."""

    command_registry.register(
        Command(
            name="tasks",
            description="列出后台 SubAgent 任务。",
            usage="/tasks",
            type=CommandType.LOCAL,
            handler=lambda ctx, invocation: _handle_tasks(task_manager),
            allowed_while_generating=True,
            allowed_during_confirmation=True,
        )
    )
    command_registry.register(
        Command(
            name="task",
            description="查看或取消后台 SubAgent 任务。",
            usage="/task <info|cancel> <task_id>",
            type=CommandType.LOCAL,
            argument_hint="<info|cancel> <task_id>",
            handler=lambda ctx, invocation: _handle_task(task_manager, trace_registry, invocation),
            allowed_while_generating=True,
            allowed_during_confirmation=True,
        )
    )


def _handle_tasks(task_manager: TaskManager) -> CommandResult:
    records = task_manager.list_tasks()
    if not records:
        return CommandResult(message="当前没有后台 SubAgent 任务。")
    lines = ["后台 SubAgent 任务："]
    for record in records:
        lines.append(
            f"- {record.task_id} | {record.status.value} | {record.launch_mode.value} | {record.request.subagent_type or 'fork'} | {record.summary}"
        )
    return CommandResult(message="\n".join(lines))


def _handle_task(task_manager: TaskManager, trace_registry: TraceRegistry | None, invocation: CommandInvocation) -> CommandResult:
    parts = invocation.args.split(maxsplit=1)
    if len(parts) != 2 or parts[0] not in {"info", "cancel"}:
        return CommandResult(message="用法：/task info <task_id> 或 /task cancel <task_id>")
    action, task_id = parts
    if action == "cancel":
        _ok, message = task_manager.cancel(task_id)
        return CommandResult(message=message)
    record = task_manager.get(task_id)
    if record is None:
        return CommandResult(message=f"未知后台任务：{task_id}")
    trace = trace_registry.for_task(task_id) if trace_registry is not None else None
    lines = [
        f"任务：{record.task_id}",
        f"状态：{record.status.value}",
        f"模式：{record.launch_mode.value}",
        f"类型：{record.request.subagent_type or 'fork'}",
        f"摘要：{record.summary}",
        f"开始：{record.started_at or 'unknown'}",
        f"结束：{record.ended_at or 'unknown'}",
        f"Token：{record.usage.render()}",
        f"Trace：{record.trace_id or '无'}",
    ]
    if trace is not None:
        lines.append(f"Trace 状态：{trace.status.value}")
        lines.append(f"Trace 错误：{trace.error_summary or '无'}")
    if record.error:
        lines.append(f"错误：{record.error}")
    if record.result:
        lines.append(f"结果：\n{record.result}")
    return CommandResult(message="\n".join(lines))
