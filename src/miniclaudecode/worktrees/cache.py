"""Cache invalidation hooks for worktree directory switches."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WorkdirSwitchResult:
    """Observable result of switching MiniClaudeCode runtime workdir."""

    workdir: Path
    message: str


class CacheInvalidator:
    """Call an application-provided dependency refresh callback."""

    def __init__(self, callback: Callable[[Path], None] | None = None) -> None:
        self.callback = callback

    def switch(self, workdir: str | Path) -> WorkdirSwitchResult:
        resolved = Path(workdir).resolve()
        if self.callback is not None:
            self.callback(resolved)
        return WorkdirSwitchResult(resolved, f"已刷新工作目录相关缓存：{resolved}")
