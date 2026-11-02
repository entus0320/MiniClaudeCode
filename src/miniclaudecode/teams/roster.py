"""Roster helpers for AgentTeam members."""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path

from miniclaudecode.teams.core import AgentTeam, TeamBackendKind, TeamMember, TeamMemberStatus
from miniclaudecode.teams.ids import new_agent_id, validate_member_name


class TeamRoster:
    """Immutable-style roster operations over an AgentTeam."""

    def add_member(
        self,
        team: AgentTeam,
        name: str,
        *,
        role: str = "member",
        backend: TeamBackendKind = TeamBackendKind.IN_PROCESS,
        workdir: Path | None = None,
    ) -> tuple[AgentTeam, TeamMember]:
        member_name = validate_member_name(name)
        existing = self.get(team, member_name)
        if existing is not None:
            return team, existing
        agent_id = new_agent_id()
        now = time.time()
        mailbox_path = team.mailbox_root / f"{agent_id}.jsonl"
        transcript_path = team.transcript_root / agent_id / "transcript.jsonl"
        member = TeamMember(
            name=member_name,
            role=role,
            agent_id=agent_id,
            backend=backend,
            workdir=team.root if workdir is None else workdir,
            mailbox_path=mailbox_path,
            transcript_path=transcript_path,
            status=TeamMemberStatus.IDLE,
            created_at=now,
            updated_at=now,
            last_active_at=now,
        )
        updated = replace(team, members=(*team.members, member), updated_at=now)
        return updated, member

    def get(self, team: AgentTeam, name: str) -> TeamMember | None:
        member_name = validate_member_name(name)
        for member in team.members:
            if member.name == member_name:
                return member
        return None

    def update_member(self, team: AgentTeam, member: TeamMember) -> AgentTeam:
        now = time.time()
        updated_member = replace(member, updated_at=now, last_active_at=now)
        members = tuple(updated_member if item.name == member.name else item for item in team.members)
        return replace(team, members=members, updated_at=now)

    def update_status(self, team: AgentTeam, name: str, status: TeamMemberStatus) -> AgentTeam:
        member = self.get(team, name)
        if member is None:
            return team
        return self.update_member(team, replace(member, status=status))
