"""Worktree binding helpers for AgentTeam members."""

from __future__ import annotations

from miniclaudecode.teams.core import TeamMember, TeamOperationResult
from miniclaudecode.worktrees.manager import WorktreeManager


class TeamWorktreeBinder:
    """Create or reuse worktrees for team members."""

    def __init__(self, manager: WorktreeManager) -> None:
        self.manager = manager

    def ensure_member_worktree(self, team_name: str, member: TeamMember) -> TeamOperationResult:
        name = f"teams/{team_name}/{member.name}"
        result = self.manager.create(name, temporary=False)
        if not result.success:
            return TeamOperationResult(False, result.message)
        return TeamOperationResult(True, result.message, metadata={"worktree": name})
