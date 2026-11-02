"""Core data models for AgentTeam orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class TeamError(Exception):
    """Raised when AgentTeam state or operations fail."""


class TeamBackendKind(Enum):
    """Supported AgentTeam member execution backends."""

    TMUX_PANE = "tmux_pane"
    ITERM2_PANE = "iterm2_pane"
    IN_PROCESS = "in_process"


class TeamMemberStatus(Enum):
    """Lifecycle state for one team member."""

    PENDING = "pending"
    RUNNING = "running"
    IDLE = "idle"
    FAILED = "failed"
    UNRECOVERABLE = "unrecoverable"
    STOPPED = "stopped"
    ARCHIVED = "archived"


class SharedTaskStatus(Enum):
    """Shared task board status values."""

    TODO = "todo"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"


class TeamMessageKind(Enum):
    """Mailbox message categories."""

    TEXT = "text"
    BROADCAST = "broadcast"
    LIFECYCLE = "lifecycle"
    APPROVAL_REPLY = "approval_reply"
    TASK_UPDATE = "task_update"


@dataclass(frozen=True)
class AgentTeamConfig:
    """Configuration for AgentTeam state and runtime behavior."""

    enabled: bool = True
    root_dir: Path = Path(".miniclaudecode/teams")
    backend_priority: tuple[str, ...] = (TeamBackendKind.IN_PROCESS.value, TeamBackendKind.TMUX_PANE.value, TeamBackendKind.ITERM2_PANE.value)
    allow_backend_fallback: bool = False
    mailbox_dir: Path = Path("mailboxes")
    transcript_dir: Path = Path("transcripts")
    tasks_file: Path = Path("tasks.json")
    registry_file: Path = Path("registry.json")
    default_permission_mode: str = "default"
    coordinator_enabled: bool = False


@dataclass(frozen=True)
class TeamDiagnostic:
    """User-visible diagnostic emitted by AgentTeam operations."""

    message: str
    path: Path | None = None

    def render(self) -> str:
        return f"{self.message}\n- 路径：{self.path}" if self.path is not None else self.message


@dataclass(frozen=True)
class TeamOperationResult:
    """Structured result from one AgentTeam operation."""

    success: bool
    message: str
    requires_confirmation: bool = False
    diagnostics: tuple[TeamDiagnostic, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TeamMember:
    """Persistent roster entry for one AgentTeam member."""

    name: str
    role: str
    agent_id: str
    backend: TeamBackendKind
    workdir: Path
    mailbox_path: Path
    transcript_path: Path
    permission_mode: str = "default"
    status: TeamMemberStatus = TeamMemberStatus.PENDING
    backend_ref: dict[str, str] = field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0
    last_active_at: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentTeam:
    """Persistent AgentTeam object."""

    team_id: str
    name: str
    lead_id: str | None
    root: Path
    config: AgentTeamConfig
    members: tuple[TeamMember, ...] = ()
    created_at: float = 0.0
    updated_at: float = 0.0
    archived: bool = False
    diagnostics: tuple[TeamDiagnostic, ...] = ()

    @property
    def tasks_path(self) -> Path:
        return self.root / self.config.tasks_file

    @property
    def registry_path(self) -> Path:
        return self.root / self.config.registry_file

    @property
    def mailbox_root(self) -> Path:
        return self.root / self.config.mailbox_dir

    @property
    def transcript_root(self) -> Path:
        return self.root / self.config.transcript_dir


@dataclass(frozen=True)
class SharedTask:
    """One persistent task on an AgentTeam shared task board."""

    task_id: str
    title: str
    description: str = ""
    status: SharedTaskStatus = SharedTaskStatus.TODO
    owner_member: str | None = None
    owner_agent_id: str | None = None
    dependencies: tuple[str, ...] = ()
    created_by: str | None = None
    created_at: float = 0.0
    updated_at: float = 0.0
    result_summary: str = ""


@dataclass(frozen=True)
class TeamMessage:
    """One mailbox message addressed to a team member agentID."""

    message_id: str
    team_id: str
    from_agent_id: str | None
    from_member: str | None
    to_agent_id: str
    to_member: str | None
    kind: TeamMessageKind
    summary: str
    body: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    processed_at: float | None = None


@dataclass(frozen=True)
class BackendDetection:
    """Detection result for one execution backend."""

    kind: TeamBackendKind
    available: bool
    message: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BackendStartResult:
    """Result from starting one member backend."""

    success: bool
    message: str
    backend_ref: dict[str, str] = field(default_factory=dict)
    diagnostics: tuple[TeamDiagnostic, ...] = ()
