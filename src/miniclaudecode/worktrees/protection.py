"""Deletion protection checks for managed worktrees."""

from __future__ import annotations

from pathlib import Path

from miniclaudecode.worktrees.core import WorktreeProtectionResult
from miniclaudecode.worktrees.git import WorktreeGitClient


class WorktreeProtection:
    """Check whether a worktree can be deleted safely."""

    def __init__(self, git_client: WorktreeGitClient) -> None:
        self.git_client = git_client

    def check(self, path: str | Path) -> WorktreeProtectionResult:
        try:
            dirty = bool(self.git_client.status_porcelain(path).strip())
            unpushed = self.git_client.has_unpushed_commits(path)
        except Exception as exc:
            return WorktreeProtectionResult(
                safe=False,
                check_failed=True,
                reason=f"Git 检查失败，拒绝自动删除：{exc}",
            )
        if dirty:
            return WorktreeProtectionResult(safe=False, dirty=True, reason="Worktree 存在未提交修改。")
        if unpushed:
            return WorktreeProtectionResult(safe=False, unpushed=True, reason="Worktree 存在未推送 commit 或没有 upstream。")
        return WorktreeProtectionResult(safe=True, reason="Worktree 干净且无未推送 commit。")
