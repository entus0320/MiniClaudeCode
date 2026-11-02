"""Unified Agent tool entry point."""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from miniclaudecode.subagents.core import AgentDefinitionError, SubAgentLaunchMode, SubAgentRunResult, SubAgentSpawnRequest, SubAgentStatus
from miniclaudecode.subagents.registry import AgentDefinitionRegistry
from miniclaudecode.subagents.runtime import SubAgentRuntime
from miniclaudecode.subagents.tasks import TaskManager
from miniclaudecode.tools.base import Tool, ToolContext, ToolResult, require_string


@dataclass
class SubAgentLauncher:
    """Launch foreground or background SubAgent runs for AgentTool."""

    registry: AgentDefinitionRegistry
    runtime: SubAgentRuntime
    task_manager: TaskManager

    def launch(self, request: SubAgentSpawnRequest) -> SubAgentRunResult | str:
        definition = None
        launch_mode = SubAgentLaunchMode.FORK
        if request.subagent_type:
            definition = self.registry.get(request.subagent_type)
            launch_mode = SubAgentLaunchMode.DEFINITION
        background = request.background or launch_mode is SubAgentLaunchMode.FORK
        normalized_request = SubAgentSpawnRequest(
            task=request.task,
            subagent_type=request.subagent_type,
            background=background,
            timeout_seconds=request.timeout_seconds,
            tools=request.tools,
            disallowed_tools=request.disallowed_tools,
            metadata=request.metadata,
        )
        if background:
            record = self.task_manager.start_background(
                normalized_request,
                launch_mode=launch_mode,
                subagent_type=request.subagent_type,
                run_callable=lambda cancel_event, trace_id, task_id: self.runtime.run_to_completion(
                    normalized_request,
                    definition=definition,
                    cancel_event=cancel_event,
                    trace_id=trace_id,
                    task_id=task_id,
                ),
            )
            return record.task_id
        return self.runtime.run_to_completion(normalized_request, definition=definition, cancel_event=threading.Event())


class AgentTool(Tool):
    """Start a SubAgent through one stable tool schema."""

    name = "agent"
    description = "启动一个子 Agent 完成指定任务。指定 subagent_type 走定义式路径，留空走 Fork 后台路径。"
    input_schema = {
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "子 Agent 要完成的任务。"},
            "subagent_type": {"type": "string", "description": "可选 AgentDefinition 名称；留空时使用 Fork 路径。"},
            "background": {"type": "boolean", "description": "是否后台运行。Fork 路径总是后台运行。"},
            "timeout_seconds": {"type": "number", "description": "可选超时时间。"},
            "tools": {"type": "array", "items": {"type": "string"}, "description": "可选运行时工具白名单。"},
            "disallowedTools": {"type": "array", "items": {"type": "string"}, "description": "可选运行时工具黑名单。"},
        },
        "required": ["task"],
        "additionalProperties": False,
    }
    requires_confirmation = False

    def __init__(self, launcher: SubAgentLauncher) -> None:
        self.launcher = launcher

    def execute(self, arguments: Mapping[str, Any], context: ToolContext) -> ToolResult:
        _ = context
        task = require_string(arguments, "task", self.name)
        if isinstance(task, ToolResult):
            return task
        subagent_type = arguments.get("subagent_type")
        if subagent_type is not None and not isinstance(subagent_type, str):
            return ToolResult.fail(tool_name=self.name, error="参数 `subagent_type` 必须是字符串。")
        background = arguments.get("background", False)
        if not isinstance(background, bool):
            return ToolResult.fail(tool_name=self.name, error="参数 `background` 必须是布尔值。")
        timeout = arguments.get("timeout_seconds")
        if timeout is not None and (not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or float(timeout) <= 0):
            return ToolResult.fail(tool_name=self.name, error="参数 `timeout_seconds` 必须是正数。")
        tools = _optional_str_tuple(arguments.get("tools"), "tools")
        if isinstance(tools, ToolResult):
            return tools
        disallowed = _optional_str_tuple(arguments.get("disallowedTools"), "disallowedTools")
        if isinstance(disallowed, ToolResult):
            return disallowed
        request = SubAgentSpawnRequest(
            task=task,
            subagent_type=subagent_type.strip() if isinstance(subagent_type, str) and subagent_type.strip() else None,
            background=background,
            timeout_seconds=float(timeout) if timeout is not None else None,
            tools=tools,
            disallowed_tools=disallowed or (),
        )
        try:
            result = self.launcher.launch(request)
        except AgentDefinitionError as exc:
            return ToolResult.fail(tool_name=self.name, error=str(exc))
        except Exception as exc:
            return ToolResult.fail(tool_name=self.name, error=f"子 Agent 启动失败：{exc}")
        if isinstance(result, str):
            return ToolResult.ok(
                tool_name=self.name,
                content=f"子 Agent 已在后台启动。task id：{result}。可用 /task info {result} 查看详情。",
                metadata={"task_id": result, "background": True},
            )
        if result.success:
            return ToolResult.ok(
                tool_name=self.name,
                content=result.final_text,
                metadata={"trace_id": result.trace_id, "status": result.status.value},
            )
        return ToolResult.fail(
            tool_name=self.name,
            error=result.error or result.final_text or f"子 Agent 失败：{result.status.value}",
            metadata={"trace_id": result.trace_id, "status": result.status.value},
        )


def _optional_str_tuple(value: object, field_name: str) -> tuple[str, ...] | None | ToolResult:
    if value is None:
        return None
    if not isinstance(value, list):
        return ToolResult.fail(tool_name="agent", error=f"参数 `{field_name}` 必须是字符串数组。")
    items: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            return ToolResult.fail(tool_name="agent", error=f"参数 `{field_name}[{index}]` 必须是非空字符串。")
        items.append(item.strip())
    return tuple(items)
