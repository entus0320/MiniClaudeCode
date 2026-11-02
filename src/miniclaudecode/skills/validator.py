"""Validation helpers for Skills."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from miniclaudecode.commands.core import CommandRegistry
from miniclaudecode.skills.core import SkillContextStrategy, SkillMetadata, SkillMode, SkillValidationError
from miniclaudecode.tools.registry import ToolRegistry


def normalize_skill_metadata(raw: Mapping[str, Any], *, path: Path) -> SkillMetadata:
    """Normalize raw frontmatter into validated metadata."""

    name = _required_string(raw, "name", path)
    validate_skill_name(name)
    description = _required_string(raw, "description", path)
    tools = _parse_tools(raw.get("tools"), path)
    mode = _parse_mode(raw.get("mode"), path)
    model = _optional_string(raw.get("model"), "model", path)
    context_strategy = _parse_context(raw.get("context"), path)
    recent_messages = _parse_recent_messages(raw.get("recent_messages"), context_strategy, path)
    return SkillMetadata(
        name=name.strip().lower(),
        description=description.strip(),
        tools=tools,
        mode=mode,
        model=model,
        context_strategy=context_strategy,
        recent_messages=recent_messages,
    )


def validate_skill_name(name: str) -> None:
    """Require a name that can safely become a slash command."""

    value = name.strip()
    if not value:
        raise SkillValidationError("Skill 名称不能为空。")
    if any(char.isspace() for char in value):
        raise SkillValidationError(f"Skill 名称不能包含空白字符：{name}")
    if "/" in value or "\\" in value:
        raise SkillValidationError(f"Skill 名称不能包含路径分隔符：{name}")


def validate_skill_against_registries(
    skill: SkillMetadata,
    *,
    tool_registry: ToolRegistry,
    command_registry: CommandRegistry | None = None,
) -> None:
    """Fail fast for command conflicts and missing whitelisted tools."""

    if command_registry is not None and command_registry.get(skill.name) is not None:
        raise SkillValidationError(f"Skill 命令名与已有命令冲突：/{skill.name}")
    for tool_name in skill.tools:
        if tool_name not in tool_registry:
            raise SkillValidationError(f"Skill `{skill.name}` 引用了不存在的工具：{tool_name}")


def validate_directory_tool_schema(schema: Mapping[str, Any], *, path: Path) -> None:
    """Require strict object JSON schema for directory Skill tools."""

    if schema.get("type") != "object":
        raise SkillValidationError(f"目录工具 schema 必须声明 type: object：{path}")
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        raise SkillValidationError(f"目录工具 schema.properties 必须是对象：{path}")
    if schema.get("additionalProperties") is not False:
        raise SkillValidationError(f"目录工具 schema 必须设置 additionalProperties: false：{path}")
    required = schema.get("required", [])
    if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
        raise SkillValidationError(f"目录工具 schema.required 必须是字符串数组：{path}")
    for item in required:
        if item not in properties:
            raise SkillValidationError(f"目录工具 required 字段未在 properties 中声明：{item}")


def _required_string(raw: Mapping[str, Any], key: str, path: Path) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SkillValidationError(f"Skill frontmatter 缺少非空 `{key}`：{path}")
    return value


def _optional_string(value: Any, key: str, path: Path) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise SkillValidationError(f"Skill frontmatter `{key}` 必须是非空字符串：{path}")
    return value.strip()


def _parse_tools(value: Any, path: Path) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise SkillValidationError(f"Skill frontmatter `tools` 必须是非空字符串数组：{path}")
    return tuple(item.strip() for item in value)


def _parse_mode(value: Any, path: Path) -> SkillMode:
    if value is None:
        return SkillMode.SHARED
    if not isinstance(value, str):
        raise SkillValidationError(f"Skill frontmatter `mode` 必须是字符串：{path}")
    try:
        return SkillMode(value.strip())
    except ValueError as exc:
        raise SkillValidationError(f"未知 Skill mode：{value}") from exc


def _parse_context(value: Any, path: Path) -> SkillContextStrategy:
    if value is None:
        return SkillContextStrategy.FULL_SUMMARY
    if not isinstance(value, str):
        raise SkillValidationError(f"Skill frontmatter `context` 必须是字符串：{path}")
    try:
        return SkillContextStrategy(value.strip())
    except ValueError as exc:
        raise SkillValidationError(f"未知 Skill context：{value}") from exc


def _parse_recent_messages(value: Any, strategy: SkillContextStrategy, path: Path) -> int | None:
    if strategy is not SkillContextStrategy.RECENT:
        return None
    if not isinstance(value, int) or value < 1:
        raise SkillValidationError(f"context: recent 需要正整数 recent_messages：{path}")
    return value
