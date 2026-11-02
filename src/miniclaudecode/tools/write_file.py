"""Write file tool."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from miniclaudecode.tools.base import Tool, ToolContext, ToolResult, require_string
from miniclaudecode.tools.paths import (
    is_policy_file_path,
    is_sensitive_path,
    relative_to_workdir,
    resolve_workspace_path,
    sensitive_path_failure,
)


class WriteFileTool(Tool):
    name = "write_file"
    description = "Create or overwrite a UTF-8 text file inside the project workspace. Requires user confirmation; for small edits to existing files, read the file first and prefer edit_file."
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to write, relative to the project root."},
            "content": {"type": "string", "description": "Full text content to write."},
        },
        "required": ["path", "content"],
        "additionalProperties": False,
    }
    requires_confirmation = True

    def execute(self, arguments: Mapping[str, Any], context: ToolContext) -> ToolResult:
        path_arg = require_string(arguments, "path", self.name)
        if isinstance(path_arg, ToolResult):
            return path_arg
        content = require_string(arguments, "content", self.name)
        if isinstance(content, ToolResult):
            return content

        path = resolve_workspace_path(path_arg, context, self.name)
        if isinstance(path, ToolResult):
            return path
        if is_policy_file_path(path, context):
            return ToolResult.fail(tool_name=self.name, error="项目权限策略文件不允许通过工具写入。")
        if is_sensitive_path(path, context):
            return sensitive_path_failure(self.name)
        if not path.parent.exists():
            return ToolResult.fail(tool_name=self.name, error="父目录不存在，已拒绝写入。")
        if path.exists() and not path.is_file():
            return ToolResult.fail(tool_name=self.name, error="目标不是普通文件。")

        try:
            path.write_text(content, encoding="utf-8")
        except Exception as exc:
            return ToolResult.fail(tool_name=self.name, error=f"写入文件失败：{exc}")

        return ToolResult.ok(
            tool_name=self.name,
            content="文件写入成功。",
            metadata={"path": relative_to_workdir(path, context), "bytes_written": len(content.encode("utf-8"))},
        )
