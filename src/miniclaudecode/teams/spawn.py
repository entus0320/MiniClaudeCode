"""Member spawn facade for AgentTeam."""

from __future__ import annotations

from miniclaudecode.teams.backends import BackendStartRequest, TeamBackend
from miniclaudecode.teams.core import TeamOperationResult


class MemberSpawner:
    """Start members through a selected backend."""

    def __init__(self, backend: TeamBackend) -> None:
        self.backend = backend

    def spawn(self, request: BackendStartRequest) -> TeamOperationResult:
        result = self.backend.start_member(request)
        if result.success:
            return TeamOperationResult(True, result.message, metadata=result.backend_ref)
        return TeamOperationResult(False, result.message, diagnostics=result.diagnostics)
