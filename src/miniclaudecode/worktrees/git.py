"""Git command wrapper for managed worktrees."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from miniclaudecode.worktrees.core import WorktreeError


@dataclass(frozen=True)
class GitCommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class WorktreeGitClient:
    """Small wrapper around the git commands needed by WorktreeManager."""

    def __init__(self, repo_root: str | Path) -> None:
        self.repo_root = Path(repo_root).resolve()

    def repo_toplevel(self, cwd: str | Path | None = None) -> Path:
        result = self._run(["rev-parse", "--show-toplevel"], cwd=cwd or self.repo_root)
        if result.returncode != 0:
            raise WorktreeError(_git_error("无法识别 Git 仓库根目录", result))
        return Path(result.stdout.strip()).resolve()

    def current_branch(self, cwd: str | Path) -> str:
        result = self._run(["rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd)
        if result.returncode != 0:
            raise WorktreeError(_git_error("无法读取当前分支", result))
        return result.stdout.strip()

    def current_head(self, cwd: str | Path) -> str:
        result = self._run(["rev-parse", "HEAD"], cwd=cwd)
        if result.returncode != 0:
            raise WorktreeError(_git_error("无法读取 HEAD", result))
        return result.stdout.strip()

    def add_worktree(self, path: str | Path, branch: str, *, base_ref: str | None = None) -> None:
        args = ["worktree", "add", "-b", branch, str(Path(path))]
        if base_ref:
            args.append(base_ref)
        result = self._run(args, cwd=self.repo_root)
        if result.returncode != 0:
            raise WorktreeError(_git_error("创建 Git worktree 失败", result))

    def remove_worktree(self, path: str | Path, *, force: bool = False) -> None:
        args = ["worktree", "remove"]
        if force:
            args.append("--force")
        args.append(str(Path(path)))
        result = self._run(args, cwd=self.repo_root)
        if result.returncode != 0:
            raise WorktreeError(_git_error("删除 Git worktree 失败", result))

    def status_porcelain(self, cwd: str | Path) -> str:
        result = self._run(["status", "--porcelain"], cwd=cwd)
        if result.returncode != 0:
            raise WorktreeError(_git_error("读取 Git 状态失败", result))
        return result.stdout

    def has_upstream(self, cwd: str | Path) -> bool:
        result = self._run(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], cwd=cwd)
        return result.returncode == 0 and bool(result.stdout.strip())

    def has_unpushed_commits(self, cwd: str | Path) -> bool:
        if not self.has_upstream(cwd):
            return True
        result = self._run(["rev-list", "--count", "@{u}..HEAD"], cwd=cwd)
        if result.returncode != 0:
            raise WorktreeError(_git_error("检查未推送 commit 失败", result))
        try:
            return int(result.stdout.strip() or "0") > 0
        except ValueError as exc:
            raise WorktreeError("检查未推送 commit 失败：git 输出不是整数。") from exc

    def _run(self, args: list[str], *, cwd: str | Path) -> GitCommandResult:
        completed = subprocess.run(
            ["git", *args],
            cwd=Path(cwd),
            text=True,
            capture_output=True,
            check=False,
        )
        return GitCommandResult(completed.returncode, completed.stdout, completed.stderr)


def _git_error(prefix: str, result: GitCommandResult) -> str:
    detail = (result.stderr or result.stdout).strip()
    return f"{prefix}：{detail or result.returncode}"
