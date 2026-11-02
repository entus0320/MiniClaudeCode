"""Core data types for Git worktree isolation."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class WorktreeError(Exception):
    """Raised when worktree setup or execution fails."""


class WorktreeStatus(str, Enum):
    """Managed worktree lifecycle state."""

    READY = "ready"
    ACTIVE = "active"
    MISSING = "missing"
    DELETED = "deleted"
    ERROR = "error"


class WorktreeTaskStatus(str, Enum):
    """Persisted task state across process restarts."""

    RECORDED = "recorded"
    UNRECOVERABLE = "unrecoverable"


@dataclass(frozen=True)
class WorktreeConfig:
    """Runtime configuration for Git worktree isolation."""

    enabled: bool = True
    root_dir: Path = Path(".miniclaudecode/worktrees")
    state_file: Path = Path(".miniclaudecode/worktrees/state.json")
    branch_prefix: str = "miniclaudecode/"
    max_name_length: int = 64
    max_segment_length: int = 32
    copy_files: tuple[str, ...] = ("settings.local.json",)
    link_dirs: tuple[str, ...] = ()
    ignored_file_rules: tuple["IgnoredFileRule", ...] = ()
    temporary_ttl_seconds: float = 7 * 24 * 60 * 60
    cleanup_interval_seconds: float = 300.0
    confirm_required_for_dirty_delete: bool = True


@dataclass(frozen=True)
class IgnoredFileRule:
    """One best-effort file copy rule for gitignored runtime files."""

    path: str
    required: bool = False


@dataclass(frozen=True)
class WorktreeDiagnostic:
    """User-visible worktree diagnostic."""

    message: str
    path: Path | None = None

    def render(self) -> str:
        if self.path is None:
            return f"Worktree 诊断：{self.message}"
        return f"Worktree 诊断：{self.message}；路径：{self.path}"


@dataclass(frozen=True)
class WorktreeRecord:
    """Persisted metadata for one managed worktree."""

    name: str
    path: Path
    branch: str
    status: WorktreeStatus = WorktreeStatus.READY
    created_at: float = 0.0
    updated_at: float = 0.0
    temporary: bool = False
    head: str | None = None
    diagnostics: tuple[WorktreeDiagnostic, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WorktreeTaskLink:
    """Persisted relation between a background task and a worktree."""

    task_id: str
    worktree_name: str
    status: WorktreeTaskStatus = WorktreeTaskStatus.RECORDED
    summary: str = ""


@dataclass(frozen=True)
class WorktreeState:
    """Complete persisted worktree state."""

    records: tuple[WorktreeRecord, ...] = ()
    active_name: str | None = None
    task_links: tuple[WorktreeTaskLink, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorktreeProtectionResult:
    """Deletion safety check result."""

    safe: bool
    dirty: bool = False
    unpushed: bool = False
    check_failed: bool = False
    reason: str = ""

    @property
    def requires_confirmation(self) -> bool:
        return not self.safe


@dataclass(frozen=True)
class WorktreeOperationResult:
    """Result for a user-facing worktree operation."""

    success: bool
    message: str
    record: WorktreeRecord | None = None
    diagnostics: tuple[WorktreeDiagnostic, ...] = ()
    requires_confirmation: bool = False


@dataclass(frozen=True)
class WorktreeIsolation:
    """AgentDefinition-level worktree isolation configuration."""

    enabled: bool = False
    name: str | None = None
    temporary: bool = True
    keep_on_finish: bool = False


@dataclass(frozen=True)
class WorktreeSession:
    """A worktree acquired for one isolated run."""

    record: WorktreeRecord
    translation_instruction: str
    cleanup_required: bool = True
