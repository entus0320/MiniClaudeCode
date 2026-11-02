"""Execution backend interfaces for AgentTeam members."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from miniclaudecode.teams.core import BackendDetection, BackendStartResult, TeamBackendKind, TeamMember, TeamMessage, TeamOperationResult


@dataclass(frozen=True)
class BackendStartRequest:
    """Request to start one AgentTeam member."""

    team_id: str
    member: TeamMember
    task: str = ""


class TeamBackend(Protocol):
    kind: TeamBackendKind

    def detect(self) -> BackendDetection: ...

    def start_member(self, request: BackendStartRequest) -> BackendStartResult: ...

    def wake(self, member: TeamMember, message: TeamMessage) -> TeamOperationResult: ...


class BackendSelector:
    """Choose a backend from detection results without silent downgrade."""

    def __init__(self, backends: dict[TeamBackendKind, TeamBackend]) -> None:
        self.backends = dict(backends)

    def detections(self) -> tuple[BackendDetection, ...]:
        return tuple(backend.detect() for backend in self.backends.values())

    def select(self, priority: tuple[str, ...], *, allow_fallback: bool = False) -> TeamBackend:
        if not priority:
            raise ValueError("AgentTeam 后端优先级不能为空。")
        requested = TeamBackendKind(priority[0])
        backend = self.backends.get(requested)
        if backend is None:
            raise ValueError(f"未知 AgentTeam 后端：{requested.value}")
        detection = backend.detect()
        if detection.available:
            return backend
        if not allow_fallback:
            raise ValueError(f"AgentTeam 后端不可用且不会静默降级：{requested.value}。{detection.message}")
        for name in priority[1:]:
            candidate = self.backends.get(TeamBackendKind(name))
            if candidate is not None and candidate.detect().available:
                return candidate
        raise ValueError(f"没有可用 AgentTeam 后端。首选失败：{detection.message}")
