"""Validation and normalization for AgentDefinitions."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from miniclaudecode.policy import PermissionMode
from miniclaudecode.subagents.core import AgentDefinition, AgentDefinitionError, normalize_agent_name
from miniclaudecode.tools.registry import ToolRegistry
from miniclaudecode.worktrees.core import WorktreeIsolation

_AGENT_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")


def normalize_agent_definition(
    raw: Mapping[str, Any],
    *,
    body: str,
    source,
    path: Path,
    include_verify: bool = False,
) -> AgentDefinition:
    """Normalize raw frontmatter and Markdown body into an AgentDefinition."""

    name = normalize_agent_name(_require_non_empty_str(raw.get("name"), "name", path))
    if _AGENT_NAME_PATTERN.fullmatch(name) is None:
        raise AgentDefinitionError(f"AgentDefinition 名称非法：{name}（路径：{path}）")
    description = _require_non_empty_str(raw.get("description"), f"{name}.description", path)
    prompt_markdown = body.strip()
    if not prompt_markdown:
        raise AgentDefinitionError(f"AgentDefinition 正文不能为空：{name}（路径：{path}）")

    model = _optional_str(raw.get("model"), f"{name}.model", path)
    max_rounds = _optional_positive_int(raw.get("max_rounds", raw.get("maxTurns")), f"{name}.max_rounds", path)
    permission_mode = _optional_permission_mode(raw.get("permission_mode", raw.get("permissionMode")), f"{name}.permission_mode", path)
    tools = _str_tuple(raw.get("tools"), f"{name}.tools", path)
    disallowed_tools = _str_tuple(raw.get("disallowedTools", raw.get("disallowed_tools")), f"{name}.disallowedTools", path)
    enabled = _optional_bool(raw.get("enabled"), f"{name}.enabled", path, default=True)
    worktree = _optional_isolation(raw.get("isolation", raw.get("worktree")), f"{name}.isolation", path)
    if name == "verify" and not include_verify:
        enabled = False

    return AgentDefinition(
        name=name,
        description=description,
        prompt_markdown=prompt_markdown,
        source=source,
        model=model,
        max_rounds=max_rounds,
        permission_mode=permission_mode,
        tools=tools,
        disallowed_tools=disallowed_tools,
        enabled=enabled,
        worktree=worktree,
    )


def validate_agent_against_registry(definition: AgentDefinition, tool_registry: ToolRegistry | None = None) -> None:
    """Validate an AgentDefinition against currently registered tools."""

    if tool_registry is None:
        return
    for tool_name in (*definition.tools, *definition.disallowed_tools):
        if tool_name not in tool_registry:
            raise AgentDefinitionError(f"AgentDefinition {definition.name} 引用了未知工具：{tool_name}")


def _require_non_empty_str(value: Any, field_name: str, path: Path) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AgentDefinitionError(f"AgentDefinition 字段 `{field_name}` 必须是非空字符串（路径：{path}）")
    return value.strip()


def _optional_str(value: Any, field_name: str, path: Path) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise AgentDefinitionError(f"AgentDefinition 字段 `{field_name}` 必须是非空字符串（路径：{path}）")
    return value.strip()


def _optional_positive_int(value: Any, field_name: str, path: Path) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise AgentDefinitionError(f"AgentDefinition 字段 `{field_name}` 必须是正整数（路径：{path}）")
    return value


def _optional_permission_mode(value: Any, field_name: str, path: Path) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise AgentDefinitionError(f"AgentDefinition 字段 `{field_name}` 必须是非空字符串（路径：{path}）")
    try:
        return PermissionMode(value.strip().lower()).value
    except ValueError as exc:
        supported = ", ".join(mode.value for mode in PermissionMode)
        raise AgentDefinitionError(f"AgentDefinition 字段 `{field_name}` 必须是以下之一：{supported}（路径：{path}）") from exc


def _str_tuple(value: Any, field_name: str, path: Path) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise AgentDefinitionError(f"AgentDefinition 字段 `{field_name}` 必须是字符串列表（路径：{path}）")
    items: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise AgentDefinitionError(f"AgentDefinition 字段 `{field_name}[{index}]` 必须是非空字符串（路径：{path}）")
        items.append(item.strip())
    return tuple(items)


def _optional_isolation(value: Any, field_name: str, path: Path) -> WorktreeIsolation:
    if isinstance(value, Mapping):
        isolation_type = value.get("type")
        if isolation_type is not None:
            if not isinstance(isolation_type, str) or isolation_type.strip().lower() != "worktree":
                raise AgentDefinitionError(f"AgentDefinition 字段 `{field_name}.type` 必须是 worktree（路径：{path}）")
            normalized = dict(value)
            normalized.pop("type", None)
            return _optional_worktree(normalized, field_name, path)
    return _optional_worktree(value, field_name, path)


def _optional_worktree(value: Any, field_name: str, path: Path) -> WorktreeIsolation:
    if value is None:
        return WorktreeIsolation()
    if isinstance(value, bool):
        return WorktreeIsolation(enabled=value)
    if not isinstance(value, Mapping):
        raise AgentDefinitionError(f"AgentDefinition 字段 `{field_name}` 必须是布尔值或对象（路径：{path}）")
    enabled = _optional_bool(value.get("enabled"), f"{field_name}.enabled", path, default=True)
    name = _optional_str(value.get("name"), f"{field_name}.name", path)
    temporary = _optional_bool(value.get("temporary"), f"{field_name}.temporary", path, default=True)
    keep_on_finish = _optional_bool(value.get("keep_on_finish", value.get("keepOnFinish")), f"{field_name}.keep_on_finish", path, default=False)
    return WorktreeIsolation(enabled=enabled, name=name, temporary=temporary, keep_on_finish=keep_on_finish)


def _optional_bool(value: Any, field_name: str, path: Path, *, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise AgentDefinitionError(f"AgentDefinition 字段 `{field_name}` 必须是布尔值（路径：{path}）")
    return value
