"""Lead scheduling facade for AgentTeam."""

from __future__ import annotations

from miniclaudecode.teams.lifecycle import TeamLifecycleManager
from miniclaudecode.teams.core import TeamOperationResult


class LeadScheduler:
    """Small wrapper for deterministic Lead task operations."""

    def __init__(self, manager: TeamLifecycleManager) -> None:
        self.manager = manager

    def assign_task(self, team: str, title: str, owner: str) -> TeamOperationResult:
        return self.manager.create_task(team, title, owner=owner)
