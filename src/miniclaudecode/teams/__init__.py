"""AgentTeam orchestration subsystem."""

from miniclaudecode.teams.core import (
    AgentTeam,
    AgentTeamConfig,
    BackendDetection,
    BackendStartResult,
    SharedTask,
    SharedTaskStatus,
    TeamBackendKind,
    TeamDiagnostic,
    TeamError,
    TeamMember,
    TeamMemberStatus,
    TeamMessage,
    TeamMessageKind,
    TeamOperationResult,
)
from miniclaudecode.teams.lifecycle import TeamLifecycleManager
from miniclaudecode.teams.store import AgentTeamStore

__all__ = [
    "AgentTeam",
    "AgentTeamConfig",
    "AgentTeamStore",
    "BackendDetection",
    "BackendStartResult",
    "SharedTask",
    "SharedTaskStatus",
    "TeamBackendKind",
    "TeamDiagnostic",
    "TeamError",
    "TeamLifecycleManager",
    "TeamMember",
    "TeamMemberStatus",
    "TeamMessage",
    "TeamMessageKind",
    "TeamOperationResult",
]
