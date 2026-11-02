"""Periodic cleanup for temporary managed worktrees."""

from __future__ import annotations

import time
from dataclasses import replace

from miniclaudecode.worktrees.core import WorktreeDiagnostic, WorktreeStatus
from miniclaudecode.worktrees.manager import WorktreeManager


class WorktreeCleanupWorker:
    """Run fail-closed cleanup for expired temporary worktrees."""

    def __init__(self, manager: WorktreeManager) -> None:
        self.manager = manager
        self._last_run = 0.0

    def tick(self) -> tuple[WorktreeDiagnostic, ...]:
        now = time.time()
        if now - self._last_run < self.manager.config.cleanup_interval_seconds:
            return ()
        self._last_run = now
        return self.cleanup(now=now)

    def cleanup(self, *, now: float | None = None) -> tuple[WorktreeDiagnostic, ...]:
        return (*self.manager.stale_cleanup(), *self.manager.auto_cleanup(now=now))


def mark_missing_records(manager: WorktreeManager) -> None:
    for record in manager.list_records():
        if not record.path.exists() and record.status is not WorktreeStatus.MISSING:
            manager._upsert(replace(record, status=WorktreeStatus.MISSING, updated_at=time.time()))  # noqa: SLF001
