"""Member recovery helpers for AgentTeam."""

from __future__ import annotations

from miniclaudecode.teams.core import TeamMember, TeamOperationResult


class MemberRecovery:
    """Minimal recovery facade for persisted member transcript and mailbox state."""

    def recover_idle_member(self, member: TeamMember) -> TeamOperationResult:
        if not member.transcript_path.parent.exists() and not member.mailbox_path.exists():
            return TeamOperationResult(False, f"队员 {member.name} 缺少可恢复 transcript 或 mailbox。")
        return TeamOperationResult(True, f"队员 {member.name} 可从磁盘状态恢复。", metadata={"agent_id": member.agent_id})
