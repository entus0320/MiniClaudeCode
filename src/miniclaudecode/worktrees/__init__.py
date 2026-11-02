"""Git worktree isolation public API."""

from miniclaudecode.worktrees.adapter import SubAgentWorktreeAdapter
from miniclaudecode.worktrees.cleanup import WorktreeCleanupWorker
from miniclaudecode.worktrees.core import (
    IgnoredFileRule,
    WorktreeConfig,
    WorktreeDiagnostic,
    WorktreeError,
    WorktreeIsolation,
    WorktreeOperationResult,
    WorktreeProtectionResult,
    WorktreeRecord,
    WorktreeSession,
    WorktreeState,
    WorktreeStatus,
    WorktreeTaskLink,
    WorktreeTaskStatus,
)
from miniclaudecode.worktrees.manager import WorktreeManager
from miniclaudecode.worktrees.store import WorktreeStore

__all__ = [
    "IgnoredFileRule",
    "SubAgentWorktreeAdapter",
    "WorktreeCleanupWorker",
    "WorktreeConfig",
    "WorktreeDiagnostic",
    "WorktreeError",
    "WorktreeIsolation",
    "WorktreeManager",
    "WorktreeOperationResult",
    "WorktreeProtectionResult",
    "WorktreeRecord",
    "WorktreeSession",
    "WorktreeState",
    "WorktreeStatus",
    "WorktreeStore",
    "WorktreeTaskLink",
    "WorktreeTaskStatus",
]
