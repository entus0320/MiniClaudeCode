"""Directory Skill tools adapted to the standard Tool protocol."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from miniclaudecode.skills.core import DirectoryToolSpec, SkillValidationError
from miniclaudecode.tools.base import Tool, ToolContext, ToolResult, clamp_text, sanitize_text


class DirectorySkillTool(Tool):
    """A Tool implemented by a script bundled inside a directory Skill."""

    requires_confirmation = True

    def __init__(self, *, skill_name: str, spec: DirectoryToolSpec, package_path: Path) -> None:
        self.skill_name = skill_name
        self.name = f"{skill_name}__{spec.name}"
        self.description = spec.description
        self.input_schema = dict(spec.input_schema)
        self.script_path = _safe_child_path(package_path, spec.script_path)

    def execute(self, arguments: Mapping[str, Any], context: ToolContext) -> ToolResult:
        payload = json.dumps(dict(arguments), ensure_ascii=False)
        try:
            completed = subprocess.run(
                [sys.executable, str(self.script_path)],
                input=payload,
                text=True,
                capture_output=True,
                cwd=context.workdir,
                timeout=context.command_timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return ToolResult.fail(tool_name=self.name, error="目录型 Skill 工具执行超时。")
        except OSError as exc:
            return ToolResult.fail(tool_name=self.name, error=f"目录型 Skill 工具无法启动：{exc}")

        stdout = sanitize_text(completed.stdout, context)
        stderr = sanitize_text(completed.stderr, context)
        if completed.returncode != 0:
            error = stderr.strip() or f"目录型 Skill 工具退出码：{completed.returncode}"
            return ToolResult.fail(tool_name=self.name, error=error, metadata={"returncode": completed.returncode})
        try:
            raw = json.loads(stdout or "{}")
        except json.JSONDecodeError:
            content, truncated = clamp_text(stdout.strip(), context.max_output_chars)
            metadata = {"stdout_json": False}
            if truncated:
                metadata["truncated"] = True
            return ToolResult.ok(tool_name=self.name, content=content, metadata=metadata)
        if not isinstance(raw, dict):
            return ToolResult.fail(tool_name=self.name, error="目录型 Skill 工具 stdout JSON 必须是对象。")
        success = raw.get("success", True)
        content = str(raw.get("content", ""))
        error_value = raw.get("error")
        metadata = raw.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        content, truncated = clamp_text(sanitize_text(content, context), context.max_output_chars)
        if truncated:
            metadata["truncated"] = True
        if success is False:
            return ToolResult.fail(
                tool_name=self.name,
                error=str(error_value or "目录型 Skill 工具执行失败。"),
                metadata=metadata,
            )
        return ToolResult.ok(tool_name=self.name, content=content, metadata=metadata)


def build_directory_skill_tools(skill_name: str, package_path: Path, specs: Sequence[DirectoryToolSpec]) -> list[DirectorySkillTool]:
    """Build Tool adapters for one directory Skill."""

    return [DirectorySkillTool(skill_name=skill_name, spec=spec, package_path=package_path) for spec in specs]


def _safe_child_path(root: Path, child: Path) -> Path:
    root_resolved = root.resolve()
    child_resolved = child.resolve()
    try:
        child_resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise SkillValidationError(f"目录型 Skill 工具脚本跳出 Skill 目录：{child}") from exc
    if not child_resolved.exists() or not child_resolved.is_file():
        raise SkillValidationError(f"目录型 Skill 工具脚本不存在：{child}")
    if child_resolved.suffix != ".py":
        raise SkillValidationError(f"目录型 Skill 工具脚本仅支持 Python：{child}")
    return child_resolved
