"""AgentTeam tools and tool registry helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from miniclaudecode.teams.lifecycle import TeamLifecycleManager
from miniclaudecode.tools.base import Tool, ToolContext, ToolResult, require_string
from miniclaudecode.tools.registry import ToolRegistry

TEAM_COLLABORATION_TOOLS = ("Task", "SendMessage")
TEAM_TOP_LEVEL_TOOLS = ("TeamCreate", "TeamDelete", "TeamSpawn", "TeamDispatch")
TEAM_TOOLS = (*TEAM_COLLABORATION_TOOLS, *TEAM_TOP_LEVEL_TOOLS)


class TeamCreateTool(Tool):
    name = "TeamCreate"
    description = "创建一个长期存在的 AgentTeam。"
    input_schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}, "lead_id": {"type": "string"}},
        "required": ["name"],
        "additionalProperties": False,
    }
    requires_confirmation = False

    def __init__(self, manager: TeamLifecycleManager) -> None:
        self.manager = manager

    def execute(self, arguments: Mapping[str, Any], context: ToolContext) -> ToolResult:
        _ = context
        name = require_string(arguments, "name", self.name)
        if isinstance(name, ToolResult):
            return name
        lead_id = arguments.get("lead_id")
        result = self.manager.create_team(name, lead_id=lead_id if isinstance(lead_id, str) else None)
        return _operation_to_tool_result(self.name, result)


class TeamDeleteTool(Tool):
    name = "TeamDelete"
    description = "归档或删除 AgentTeam。物理删除必须显式确认。"
    input_schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}, "confirm": {"type": "boolean"}},
        "required": ["name"],
        "additionalProperties": False,
    }
    requires_confirmation = True

    def __init__(self, manager: TeamLifecycleManager) -> None:
        self.manager = manager

    def execute(self, arguments: Mapping[str, Any], context: ToolContext) -> ToolResult:
        _ = context
        name = require_string(arguments, "name", self.name)
        if isinstance(name, ToolResult):
            return name
        result = self.manager.delete_team(name, confirm=arguments.get("confirm") is True)
        return _operation_to_tool_result(self.name, result)


class TeamSpawnTool(Tool):
    name = "TeamSpawn"
    description = "向 AgentTeam 注册或复用一个队员，并写入花名册和名称注册表。"
    input_schema = {
        "type": "object",
        "properties": {
            "team": {"type": "string"},
            "member": {"type": "string"},
            "role": {"type": "string"},
            "backend": {"type": "string", "enum": ["in_process", "tmux_pane", "iterm2_pane"]},
        },
        "required": ["team", "member"],
        "additionalProperties": False,
    }
    requires_confirmation = False

    def __init__(self, manager: TeamLifecycleManager) -> None:
        self.manager = manager

    def execute(self, arguments: Mapping[str, Any], context: ToolContext) -> ToolResult:
        _ = context
        team = require_string(arguments, "team", self.name)
        if isinstance(team, ToolResult):
            return team
        member = require_string(arguments, "member", self.name)
        if isinstance(member, ToolResult):
            return member
        role = arguments.get("role") if isinstance(arguments.get("role"), str) else "member"
        backend_value = arguments.get("backend") if isinstance(arguments.get("backend"), str) else "in_process"
        from miniclaudecode.teams.core import TeamBackendKind

        try:
            backend = TeamBackendKind(backend_value)
        except ValueError:
            return ToolResult.fail(tool_name=self.name, error=f"未知 AgentTeam 后端：{backend_value}")
        result = self.manager.add_member(team, member, role=role, backend=backend)
        return _operation_to_tool_result(self.name, result)


class TeamDispatchTool(Tool):
    name = "TeamDispatch"
    description = "把任务派发给指定队员，并在该队员自己的工作目录中执行文件读写。"
    input_schema = {
        "type": "object",
        "properties": {
            "team": {"type": "string"},
            "member": {"type": "string"},
            "task": {"type": "string"},
            "path": {"type": "string"},
            "content": {"type": "string"},
            "mode": {"type": "string", "enum": ["append", "write", "read"]},
        },
        "required": ["team", "member", "task"],
        "additionalProperties": False,
    }
    requires_confirmation = False
    parallel_safe = True

    def __init__(self, manager: TeamLifecycleManager) -> None:
        self.manager = manager

    def execute(self, arguments: Mapping[str, Any], context: ToolContext) -> ToolResult:
        _ = context
        team = require_string(arguments, "team", self.name)
        if isinstance(team, ToolResult):
            return team
        member = require_string(arguments, "member", self.name)
        if isinstance(member, ToolResult):
            return member
        task = require_string(arguments, "task", self.name)
        if isinstance(task, ToolResult):
            return task
        result = self.manager.dispatch_member_task(
            team,
            member,
            task=task,
            path=arguments.get("path") if isinstance(arguments.get("path"), str) else None,
            content=arguments.get("content") if isinstance(arguments.get("content"), str) else None,
            mode=arguments.get("mode") if isinstance(arguments.get("mode"), str) else "append",
        )
        return _operation_to_tool_result(self.name, result)


class TaskTool(Tool):
    name = "Task"
    description = "在 AgentTeam 共享任务清单中创建、列举或更新任务。"
    input_schema = {
        "type": "object",
        "properties": {
            "team": {"type": "string"},
            "action": {"type": "string", "enum": ["create", "list"]},
            "title": {"type": "string"},
            "description": {"type": "string"},
            "owner": {"type": "string"},
            "dependencies": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["team", "action"],
        "additionalProperties": False,
    }
    requires_confirmation = False

    def __init__(self, manager: TeamLifecycleManager) -> None:
        self.manager = manager

    def execute(self, arguments: Mapping[str, Any], context: ToolContext) -> ToolResult:
        _ = context
        team = require_string(arguments, "team", self.name)
        if isinstance(team, ToolResult):
            return team
        action = require_string(arguments, "action", self.name)
        if isinstance(action, ToolResult):
            return action
        if action == "list":
            return ToolResult.ok(tool_name=self.name, content=self.manager.list_tasks_text(team))
        if action == "create":
            title = require_string(arguments, "title", self.name)
            if isinstance(title, ToolResult):
                return title
            deps_raw = arguments.get("dependencies", [])
            deps = tuple(item for item in deps_raw if isinstance(item, str)) if isinstance(deps_raw, list) else ()
            result = self.manager.create_task(
                team,
                title,
                description=arguments.get("description") if isinstance(arguments.get("description"), str) else "",
                owner=arguments.get("owner") if isinstance(arguments.get("owner"), str) else None,
                dependencies=deps,
            )
            return _operation_to_tool_result(self.name, result)
        return ToolResult.fail(tool_name=self.name, error="Task.action 仅支持 create 或 list。")


class SendMessageTool(Tool):
    name = "SendMessage"
    description = "向 AgentTeam 队员发送点对点或广播消息。"
    input_schema = {
        "type": "object",
        "properties": {
            "team": {"type": "string"},
            "to": {"type": "string"},
            "summary": {"type": "string"},
            "body": {"type": "string"},
            "broadcast": {"type": "boolean"},
        },
        "required": ["team", "summary", "body"],
        "additionalProperties": False,
    }
    requires_confirmation = False

    def __init__(self, manager: TeamLifecycleManager) -> None:
        self.manager = manager

    def execute(self, arguments: Mapping[str, Any], context: ToolContext) -> ToolResult:
        _ = context
        team = require_string(arguments, "team", self.name)
        if isinstance(team, ToolResult):
            return team
        summary = require_string(arguments, "summary", self.name)
        if isinstance(summary, ToolResult):
            return summary
        body = require_string(arguments, "body", self.name)
        if isinstance(body, ToolResult):
            return body
        to = arguments.get("to")
        broadcast = arguments.get("broadcast") is True
        if not broadcast and (not isinstance(to, str) or not to.strip()):
            return ToolResult.fail(tool_name=self.name, error="非广播消息必须提供 `to`。")
        result = self.manager.send_message(team, to if isinstance(to, str) else "", summary=summary, body=body, broadcast=broadcast)
        return _operation_to_tool_result(self.name, result)


class TeamToolInjector:
    """Create registries for AgentTeam members and top-level lifecycle tools."""

    def __init__(self, manager: TeamLifecycleManager) -> None:
        self.manager = manager

    def team_member_registry(self, base: ToolRegistry) -> ToolRegistry:
        registry = base.copy().without(list(TEAM_TOOLS))
        registry.register(TaskTool(self.manager))
        registry.register(SendMessageTool(self.manager))
        return registry

    def register_top_level(self, registry: ToolRegistry) -> None:
        for name, tool in (
            ("TeamCreate", TeamCreateTool(self.manager)),
            ("TeamDelete", TeamDeleteTool(self.manager)),
            ("TeamSpawn", TeamSpawnTool(self.manager)),
            ("TeamDispatch", TeamDispatchTool(self.manager)),
        ):
            if name not in registry:
                registry.register(tool)


def _operation_to_tool_result(tool_name: str, result) -> ToolResult:  # type: ignore[no-untyped-def]
    if result.success:
        return ToolResult.ok(tool_name=tool_name, content=result.message, metadata=result.metadata)
    return ToolResult.fail(tool_name=tool_name, error=result.message, metadata={"requires_confirmation": result.requires_confirmation, **result.metadata})
