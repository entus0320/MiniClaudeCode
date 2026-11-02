"""SubAgent adapter for Git worktree isolation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from miniclaudecode.worktrees.core import WorktreeError, WorktreeIsolation, WorktreeOperationResult, WorktreeSession
from miniclaudecode.worktrees.manager import WorktreeManager


class SubAgentWorktreeAdapter:
    """Acquire and release managed worktrees for isolated SubAgent runs."""

    def __init__(self, manager: WorktreeManager) -> None:
        self.manager = manager

    def acquire(self, definition: Any, *, task_id: str | None = None) -> WorktreeSession | None:
        isolation = getattr(definition, "worktree", None)
        if not isinstance(isolation, WorktreeIsolation) or not isolation.enabled:
            return None
        name = isolation.name or _default_name(getattr(definition, "name", "subagent"), task_id)
        result = self.manager.create(name, temporary=isolation.temporary)
        if not result.success:
            existing = self.manager.get(name)
            if existing is None:
                raise WorktreeError(result.message)
            record = existing
        elif result.record is not None:
            record = result.record
        else:
            raise WorktreeError("Worktree 创建没有返回记录。")
        return WorktreeSession(
            record=record,
            translation_instruction=render_path_translation(self.manager.main_workdir, record.path),
            cleanup_required=not isolation.keep_on_finish,
        )

    def execute_with_worktree(self, definition: Any, execute, *, task_id: str | None = None):  # type: ignore[no-untyped-def]
        """Run a callable inside a managed worktree lifecycle."""

        session = self.acquire(definition, task_id=task_id)
        try:
            return execute(session)
        finally:
            self.finish(session)

    executeWithWorktree = execute_with_worktree

    def finish(self, session: WorktreeSession | None) -> WorktreeOperationResult | None:
        if session is None or not session.cleanup_required:
            return None
        if not session.record.temporary:
            return WorktreeOperationResult(True, f"Worktree 已保留：{session.record.name}", session.record)
        return self.manager.delete(session.record.name, confirm_discard=False)


def render_path_translation(main_workdir: str | Path, worktree_path: str | Path) -> str:
    return (
        "当前子 Agent 在 Git Worktree 隔离目录中运行。\n"
        f"- 主工作目录：{Path(main_workdir).resolve()}\n"
        f"- 隔离 Worktree：{Path(worktree_path).resolve()}\n"
        "当你报告路径时，优先报告相对隔离 Worktree 的路径；如果需要对照主工作目录，请明确说明路径来自哪个目录。"
    )


def _default_name(definition_name: str, task_id: str | None) -> str:
    suffix = task_id or "foreground"
    return f"subagents/{definition_name}-{suffix}"
