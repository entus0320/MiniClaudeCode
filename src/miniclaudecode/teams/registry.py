"""Name registry for AgentTeam member addressing."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from miniclaudecode.teams.core import AgentTeam, TeamError, TeamMember
from miniclaudecode.teams.ids import validate_member_name


class NameRegistry:
    """Resolve member names to agentID and mailbox files."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def save_team(self, team: AgentTeam) -> None:
        payload = {
            "schema_version": 1,
            "members": {
                member.name: {
                    "agent_id": member.agent_id,
                    "mailbox_path": str(member.mailbox_path),
                    "status": member.status.value,
                }
                for member in team.members
            },
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def resolve_member(self, team: AgentTeam, name: str) -> TeamMember:
        member_name = validate_member_name(name)
        for member in team.members:
            if member.name == member_name:
                return member
        raise TeamError(f"队员不存在：{member_name}")

    def resolve_agent_id(self, team: AgentTeam, name: str) -> str:
        return self.resolve_member(team, name).agent_id

    def resolve_mailbox(self, team: AgentTeam, name: str) -> Path:
        return self.resolve_member(team, name).mailbox_path

    def load_raw(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": 1, "members": {}}
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {"schema_version": 1, "members": {}}
