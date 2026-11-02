"""Glob file matching tool."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from miniclaudecode.tools.base import Tool, ToolContext, ToolResult, require_string
from miniclaudecode.tools.paths import is_ignored_path, relative_to_workdir


class GlobFilesTool(Tool):
    name = "glob_files"
    description = "Find file paths by glob pattern inside the project workspace. Prefer glob_files over shell commands for file discovery; ignored and sensitive paths are skipped."
    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Glob pattern relative to the project root, such as src/**/*.py."},
            "max_results": {"type": "integer", "description": "Maximum number of paths to return."},
        },
        "required": ["pattern"],
        "additionalProperties": False,
    }
    requires_confirmation = False

    def execute(self, arguments: Mapping[str, Any], context: ToolContext) -> ToolResult:
        pattern = require_string(arguments, "pattern", self.name)
        if isinstance(pattern, ToolResult):
            return pattern
        if Path(pattern).is_absolute() or ".." in Path(pattern).parts:
            return ToolResult.fail(tool_name=self.name, error="glob pattern 必须是工作目录内的相对路径。")
        max_results = arguments.get("max_results", context.max_results)
        if not isinstance(max_results, int) or max_results <= 0:
            return ToolResult.fail(tool_name=self.name, error="参数 `max_results` 必须是正整数。")
        max_results = min(max_results, context.max_results)

        matches: list[str] = []
        try:
            for path in sorted(context.workdir.glob(pattern)):
                if not path.is_file() or is_ignored_path(path, context):
                    continue
                matches.append(relative_to_workdir(path, context))
                if len(matches) >= max_results:
                    break
        except Exception as exc:
            return ToolResult.fail(tool_name=self.name, error=f"查找文件失败：{exc}")

        return ToolResult.ok(
            tool_name=self.name,
            content="\n".join(matches) if matches else "未找到匹配文件。",
            metadata={"count": len(matches), "max_results": max_results, "truncated": len(matches) >= max_results},
        )
