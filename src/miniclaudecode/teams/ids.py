"""Safe identifiers for AgentTeam state."""

from __future__ import annotations

import re
import time
import uuid

from miniclaudecode.teams.core import TeamError

_SLUG_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
_MAX_NAME_LENGTH = 64


def validate_team_name(name: str) -> str:
    """Validate a user/LLM supplied team name."""

    return _validate_slug(name, "团队名称")


def validate_member_name(name: str) -> str:
    """Validate a user/LLM supplied member name."""

    return _validate_slug(name, "队员名称")


def new_agent_id(prefix: str = "agent") -> str:
    """Return a safe opaque agentID used for mailbox file names."""

    stamp = int(time.time() * 1000)
    return f"{prefix}_{stamp}_{uuid.uuid4().hex[:8]}"


def _validate_slug(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TeamError(f"{label}不能为空。")
    parsed = value.strip()
    if len(parsed) > _MAX_NAME_LENGTH:
        raise TeamError(f"{label}长度不能超过 {_MAX_NAME_LENGTH}。")
    if parsed in {".", ".."} or ".." in parsed or "/" in parsed or "\\" in parsed:
        raise TeamError(f"{label}不能包含路径分隔符或路径遍历片段。")
    if _SLUG_PATTERN.fullmatch(parsed) is None:
        raise TeamError(f"{label}只能包含字母、数字、点、下划线和短横线。")
    return parsed
