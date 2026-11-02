"""Message protocol helpers for AgentTeam mailboxes."""

from __future__ import annotations

import time
import uuid
from typing import Any

from miniclaudecode.teams.core import TeamMessage, TeamMessageKind


def new_message_id() -> str:
    return f"msg_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"


def make_text_message(
    *,
    team_id: str,
    to_agent_id: str,
    summary: str,
    body: str,
    to_member: str | None = None,
    from_agent_id: str | None = None,
    from_member: str | None = None,
    kind: TeamMessageKind = TeamMessageKind.TEXT,
    payload: dict[str, Any] | None = None,
) -> TeamMessage:
    return TeamMessage(
        message_id=new_message_id(),
        team_id=team_id,
        from_agent_id=from_agent_id,
        from_member=from_member,
        to_agent_id=to_agent_id,
        to_member=to_member,
        kind=kind,
        summary=summary,
        body=body,
        payload={} if payload is None else payload,
        created_at=time.time(),
    )
