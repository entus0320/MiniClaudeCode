"""Tool adapters for MCP server tools."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any, Protocol

from miniclaudecode.mcp.client import McpCallResult, McpToolDescription
from miniclaudecode.tools.base import Tool, ToolContext, ToolResult, clamp_text, sanitize_text

_TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class McpToolManager(Protocol):
    """Small protocol used by McpTool to call the owning manager."""

    def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> McpCallResult:
        """Call one MCP tool."""


class McpTool(Tool):
    """Expose an MCP server tool through the local Tool interface."""

    requires_confirmation = True

    def __init__(self, description: McpToolDescription, manager: McpToolManager) -> None:
        validate_mcp_tool_description(description)
        self.server_name = description.server_name
        self.remote_name = description.name
        self.name = mcp_tool_name(description.server_name, description.name)
        self.description = f"{description.description} (MCP server: {description.server_name})"
        self.input_schema = dict(description.input_schema)
        self.parallel_safe = description.parallel_safe
        self._manager = manager

    def execute(self, arguments: Mapping[str, Any], context: ToolContext) -> ToolResult:
        result = self._manager.call_tool(self.server_name, self.remote_name, dict(arguments))
        if result.success:
            content, truncated = clamp_text(sanitize_text(result.content, context), context.max_output_chars)
            metadata = dict(result.metadata)
            if truncated:
                metadata["truncated"] = True
            return ToolResult.ok(tool_name=self.name, content=content, metadata=metadata)
        error = sanitize_text(result.error or "MCP 工具调用失败。", context)
        metadata = dict(result.metadata)
        return ToolResult.fail(tool_name=self.name, error=error, metadata=metadata)

    def summarize_arguments(self, arguments: Mapping[str, Any]) -> str:
        text = json.dumps(dict(arguments), ensure_ascii=False)
        if len(text) <= 160:
            return text
        return text[:157] + "..."


def mcp_tool_name(server_name: str, remote_name: str) -> str:
    """Return the local tool name for one MCP tool."""

    return f"{server_name}__{remote_name}"


def validate_mcp_tool_description(description: McpToolDescription) -> None:
    """Validate that an MCP tool can be exposed to model providers."""

    _validate_name(description.server_name, "server name")
    _validate_name(description.name, "MCP tool name")
    schema = description.input_schema
    if schema.get("type") != "object":
        raise ValueError("MCP tool inputSchema must be an object schema.")
    if "properties" in schema and not isinstance(schema["properties"], dict):
        raise ValueError("MCP tool inputSchema.properties must be an object.")
    if "required" in schema:
        required = schema["required"]
        if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
            raise ValueError("MCP tool inputSchema.required must be a list of strings.")
    try:
        json.dumps(schema)
    except (TypeError, ValueError) as exc:
        raise ValueError("MCP tool inputSchema must be JSON serializable.") from exc


def _validate_name(value: str, label: str) -> None:
    if _TOOL_NAME_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{label} must start with a letter or underscore and contain only letters, digits, and underscores.")


# Compatibility aliases for older tests/imports during the Chapter 06 rename.
McpToolManager = McpToolManager
McpTool = McpTool
mcp_tool_name = mcp_tool_name
validate_mcp_tool_description = validate_mcp_tool_description
