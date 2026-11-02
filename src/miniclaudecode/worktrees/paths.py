"""Path resolution helpers for managed worktrees."""

from __future__ import annotations

from pathlib import Path

from miniclaudecode.worktrees.core import WorktreeConfig, WorktreeError
from miniclaudecode.worktrees.names import validate_worktree_name


class WorktreePathResolver:
    """Resolve worktree names into safe paths under the configured root."""

    def __init__(self, main_workdir: str | Path, config: WorktreeConfig) -> None:
        self.main_workdir = Path(main_workdir).resolve()
        self.config = config
        self.root = self._resolve_config_path(config.root_dir)
        self.state_file = self._resolve_config_path(config.state_file)

    def resolve(self, name: str) -> Path:
        normalized = validate_worktree_name(name, self.config)
        candidate = (self.root / Path(*normalized.split("/"))).resolve()
        if not _is_relative_to(candidate, self.root):
            raise WorktreeError("Worktree 路径越过隔离根目录，已拒绝。")
        return candidate

    def _resolve_config_path(self, path: Path) -> Path:
        candidate = path if path.is_absolute() else self.main_workdir / path
        return candidate.resolve()


def _is_relative_to(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents
