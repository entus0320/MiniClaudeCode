"""Read file tool."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from miniclaudecode.tools.base import Tool, ToolContext, ToolResult, require_string, sanitize_text
from miniclaudecode.tools.paths import (
    is_sensitive_path,
    relative_to_workdir,
    resolve_workspace_path,
    sensitive_path_failure,
)


class ReadFileTool(Tool):
    name = "read_file"
    description = "Read-only tool for UTF-8 file contents inside the project workspace. Use read_file before editing an existing file; sensitive files and paths outside the workspace are rejected."
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the file, relative to the project root."},
        },
        "required": ["path"],
        "additionalProperties": False,
    }
    requires_confirmation = False

    def execute(self, arguments: Mapping[str, Any], context: ToolContext) -> ToolResult:
        path_arg = require_string(arguments, "path", self.name)
        if isinstance(path_arg, ToolResult):
            return path_arg

        path = resolve_workspace_path(path_arg, context, self.name)
        if isinstance(path, ToolResult):
            return path
        if is_sensitive_path(path, context):
            return sensitive_path_failure(self.name)
        if not path.exists():
            return ToolResult.fail(tool_name=self.name, error="文件不存在。")
        if not path.is_file():
            return ToolResult.fail(tool_name=self.name, error="目标不是普通文件。")

        try:
            data = path.read_bytes()
        except Exception as exc:
            return ToolResult.fail(tool_name=self.name, error=f"读取文件失败：{exc}")

        truncated = len(data) > context.max_read_bytes
        if truncated:
            data = data[: context.max_read_bytes]
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return ToolResult.fail(tool_name=self.name, error="文件不是有效的 UTF-8 文本。")

        if truncated:
            text += "\n[文件内容已按大小限制截断]"
        text = sanitize_text(text, context)
        return ToolResult.ok(
            tool_name=self.name,
            content=text,
            metadata={
                "path": relative_to_workdir(path, context),
                "bytes_read": len(data),
                "truncated": truncated,
            },
        )
