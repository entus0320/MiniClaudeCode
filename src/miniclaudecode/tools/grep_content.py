"""Grep content tool."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from miniclaudecode.tools.base import Tool, ToolContext, ToolResult, require_string, sanitize_text
from miniclaudecode.tools.paths import is_ignored_path, relative_to_workdir, resolve_workspace_path


class GrepContentTool(Tool):
    name = "grep_content"
    description = "Search UTF-8 text files in the project workspace with a regular expression. Prefer grep_content over shell grep/rg commands for code or text search; sensitive paths are skipped."
    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regular expression to search for."},
            "path": {"type": "string", "description": "Optional relative directory or file to search."},
            "glob": {"type": "string", "description": "Optional glob filter relative to path, such as **/*.py."},
            "max_results": {"type": "integer", "description": "Maximum number of matching lines to return."},
        },
        "required": ["pattern"],
        "additionalProperties": False,
    }
    requires_confirmation = False

    def execute(self, arguments: Mapping[str, Any], context: ToolContext) -> ToolResult:
        pattern = require_string(arguments, "pattern", self.name)
        if isinstance(pattern, ToolResult):
            return pattern
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            return ToolResult.fail(tool_name=self.name, error=f"正则表达式无效：{exc}")

        root_arg = arguments.get("path", ".")
        if not isinstance(root_arg, str) or root_arg == "":
            return ToolResult.fail(tool_name=self.name, error="参数 `path` 必须是非空字符串。")
        root = resolve_workspace_path(root_arg, context, self.name)
        if isinstance(root, ToolResult):
            return root

        glob_filter = arguments.get("glob", "**/*")
        if not isinstance(glob_filter, str) or Path(glob_filter).is_absolute() or ".." in Path(glob_filter).parts:
            return ToolResult.fail(tool_name=self.name, error="参数 `glob` 必须是工作目录内的相对 glob。")
        max_results = arguments.get("max_results", context.max_results)
        if not isinstance(max_results, int) or max_results <= 0:
            return ToolResult.fail(tool_name=self.name, error="参数 `max_results` 必须是正整数。")
        max_results = min(max_results, context.max_results)

        paths = [root] if root.is_file() else sorted(root.glob(glob_filter))
        results: list[str] = []
        try:
            for path in paths:
                if not path.is_file() or is_ignored_path(path, context):
                    continue
                if path.stat().st_size > context.max_read_bytes:
                    continue
                try:
                    lines = path.read_text(encoding="utf-8").splitlines()
                except UnicodeDecodeError:
                    continue
                for line_number, line in enumerate(lines, start=1):
                    if regex.search(line):
                        safe_line = sanitize_text(line, context)
                        results.append(f"{relative_to_workdir(path, context)}:{line_number}: {safe_line}")
                        if len(results) >= max_results:
                            break
                if len(results) >= max_results:
                    break
        except Exception as exc:
            return ToolResult.fail(tool_name=self.name, error=f"搜索内容失败：{exc}")

        return ToolResult.ok(
            tool_name=self.name,
            content="\n".join(results) if results else "未找到匹配内容。",
            metadata={"count": len(results), "max_results": max_results, "truncated": len(results) >= max_results},
        )
