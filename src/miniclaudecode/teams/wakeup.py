"""Wakeup dispatcher for AgentTeam mailbox messages."""

from __future__ import annotations

from miniclaudecode.teams.core import TeamBackendKind, TeamMember, TeamMessage, TeamOperationResult


class WakeupDispatcher:
    """Dispatch wakeups to the backend that owns a member."""

    def __init__(self, backends) -> None:  # type: ignore[no-untyped-def]
        self.backends = dict(backends)

    def wake(self, member: TeamMember, message: TeamMessage) -> TeamOperationResult:
        backend = self.backends.get(member.backend)
        if backend is None:
            return TeamOperationResult(False, f"未注册队员后端：{member.backend.value}")
        return backend.wake(member, message)


def backend_label(kind: TeamBackendKind) -> str:
    return kind.value
