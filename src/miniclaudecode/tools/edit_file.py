"""Edit file tool using unique string replacement."""

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


class EditFileTool(Tool):
    name = "edit_file"
    description = "Replace one unique old text string in a UTF-8 project file. Before calling edit_file, use read_file to inspect the target and copy exact old_text; requires user confirmation."
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path relative to the project root."},
            "old_text": {"type": "string", "description": "Exact text to replace. It must match exactly once."},
            "new_text": {"type": "string", "description": "Replacement text."},
        },
        "required": ["path", "old_text", "new_text"],
        "additionalProperties": False,
    }
    requires_confirmation = True

    def execute(self, arguments: Mapping[str, Any], context: ToolContext) -> ToolResult:
        path_arg = require_string(arguments, "path", self.name)
        if isinstance(path_arg, ToolResult):
            return path_arg
        old_text = require_string(arguments, "old_text", self.name)
        if isinstance(old_text, ToolResult):
            return old_text
        new_text = arguments.get("new_text")
        if not isinstance(new_text, str):
            return ToolResult.fail(tool_name=self.name, error="参数 `new_text` 必须是字符串。")

        path = resolve_workspace_path(path_arg, context, self.name)
        if isinstance(path, ToolResult):
            return path
        if is_policy_file_path(path, context):
            return ToolResult.fail(tool_name=self.name, error="项目权限策略文件不允许通过工具编辑。")
        if is_sensitive_path(path, context):
            return sensitive_path_failure(self.name)
        if not path.exists():
            return ToolResult.fail(tool_name=self.name, error="文件不存在。")
        if not path.is_file():
            return ToolResult.fail(tool_name=self.name, error="目标不是普通文件。")

        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return ToolResult.fail(tool_name=self.name, error="文件不是有效的 UTF-8 文本。")
        except Exception as exc:
            return ToolResult.fail(tool_name=self.name, error=f"读取文件失败：{exc}")

        count = content.count(old_text)
        if count == 0:
            return ToolResult.fail(tool_name=self.name, error="原文匹配不到，无法替换。")
        if count > 1:
            return ToolResult.fail(tool_name=self.name, error=f"原文匹配多次（{count} 次），无法唯一替换。")

        updated = content.replace(old_text, new_text, 1)
        try:
            path.write_text(updated, encoding="utf-8")
        except Exception as exc:
            return ToolResult.fail(tool_name=self.name, error=f"写入文件失败：{exc}")

        return ToolResult.ok(
            tool_name=self.name,
            content="文件编辑成功。",
            metadata={"path": relative_to_workdir(path, context), "replacements": 1},
        )
