"""Markdown + YAML frontmatter parsing for AgentDefinitions."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from miniclaudecode.subagents.core import AgentDefinitionError


def parse_agent_frontmatter(text: str, path: Path) -> tuple[Mapping[str, Any], str]:
    """Parse YAML frontmatter and return raw metadata plus Markdown body."""

    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise AgentDefinitionError(f"AgentDefinition 文件缺少 YAML frontmatter：{path}")
    end_index = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end_index = index
            break
    if end_index is None:
        raise AgentDefinitionError(f"AgentDefinition frontmatter 未闭合：{path}")
    raw_yaml = "\n".join(lines[1:end_index])
    try:
        data = yaml.safe_load(raw_yaml)
    except yaml.YAMLError as exc:
        raise AgentDefinitionError(f"AgentDefinition frontmatter 不是合法 YAML：{path}") from exc
    if not isinstance(data, Mapping):
        raise AgentDefinitionError(f"AgentDefinition frontmatter 必须是对象：{path}")
    body = "\n".join(lines[end_index + 1 :])
    return data, body
