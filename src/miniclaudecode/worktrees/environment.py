"""Environment initialization for managed worktrees."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from miniclaudecode.worktrees.core import IgnoredFileRule, WorktreeConfig, WorktreeDiagnostic, WorktreeError

_FORBIDDEN_FILENAMES = {".env", ".env.local", ".env.production", "config.yaml"}
_FORBIDDEN_DIR_PARTS = {".github", ".gitlab", ".circleci"}
_FORBIDDEN_SUBSTRINGS = ("token", "secret", "password")


class EnvironmentInitializer:
    """Copy and link configured runtime files into a new worktree."""

    def __init__(self, main_workdir: str | Path, config: WorktreeConfig) -> None:
        self.main_workdir = Path(main_workdir).resolve()
        self.config = config

    def initialize(self, target_workdir: str | Path) -> tuple[WorktreeDiagnostic, ...]:
        target = Path(target_workdir).resolve()
        diagnostics: list[WorktreeDiagnostic] = []
        diagnostics.extend(self._copy_allowed_files(target))
        diagnostics.extend(self._configure_hooks(target))
        diagnostics.extend(self._link_dirs(target))
        diagnostics.extend(self._copy_ignored_rules(target))
        return tuple(diagnostics)

    def _copy_allowed_files(self, target: Path) -> list[WorktreeDiagnostic]:
        diagnostics: list[WorktreeDiagnostic] = []
        for relative in self.config.copy_files:
            source = self._safe_source(relative)
            if source is None:
                diagnostics.append(WorktreeDiagnostic(f"跳过不安全的本地配置复制规则：{relative}"))
                continue
            if not source.exists():
                continue
            destination = target / relative
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
            except OSError as exc:
                diagnostics.append(WorktreeDiagnostic(f"复制本地配置失败：{exc}", source))
        return diagnostics

    def _configure_hooks(self, target: Path) -> list[WorktreeDiagnostic]:
        hooks = self.main_workdir / ".git" / "hooks"
        if not hooks.exists():
            return ()  # type: ignore[return-value]
        try:
            from miniclaudecode.worktrees.git import WorktreeGitClient

            result = WorktreeGitClient(target)._run(["config", "core.hooksPath", str(hooks)], cwd=target)
            if result.returncode != 0:
                return [WorktreeDiagnostic(f"配置 git hooksPath 失败：{(result.stderr or result.stdout).strip()}", hooks)]
        except Exception as exc:
            return [WorktreeDiagnostic(f"配置 git hooksPath 失败：{exc}", hooks)]
        return []

    def _link_dirs(self, target: Path) -> list[WorktreeDiagnostic]:
        diagnostics: list[WorktreeDiagnostic] = []
        for relative in self.config.link_dirs:
            source = self._safe_source(relative)
            if source is None:
                diagnostics.append(WorktreeDiagnostic(f"跳过不安全的依赖链接规则：{relative}"))
                continue
            if not source.exists():
                continue
            destination = target / relative
            if destination.exists():
                continue
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.symlink(source, destination, target_is_directory=source.is_dir())
            except OSError as exc:
                diagnostics.append(WorktreeDiagnostic(f"创建依赖软链接失败：{exc}", source))
        return diagnostics

    def _copy_ignored_rules(self, target: Path) -> list[WorktreeDiagnostic]:
        diagnostics: list[WorktreeDiagnostic] = []
        for rule in self.config.ignored_file_rules:
            source = self._safe_source(rule.path)
            if source is None:
                message = f"跳过不安全的 ignored 文件复制规则：{rule.path}"
                if rule.required:
                    raise WorktreeError(message)
                diagnostics.append(WorktreeDiagnostic(message))
                continue
            destination = target / rule.path
            try:
                if not source.exists():
                    if rule.required:
                        raise WorktreeError(f"required ignored 文件不存在：{rule.path}")
                    diagnostics.append(WorktreeDiagnostic(f"optional ignored 文件不存在：{rule.path}", source))
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                if source.is_dir():
                    if destination.exists():
                        continue
                    shutil.copytree(source, destination)
                else:
                    shutil.copy2(source, destination)
            except WorktreeError:
                raise
            except OSError as exc:
                if rule.required:
                    raise WorktreeError(f"required ignored 文件复制失败：{rule.path}：{exc}") from exc
                diagnostics.append(WorktreeDiagnostic(f"optional ignored 文件复制失败：{exc}", source))
        return diagnostics

    def _safe_source(self, relative: str) -> Path | None:
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            return None
        if _is_forbidden_path(path):
            return None
        source = (self.main_workdir / path).resolve()
        if not _is_relative_to(source, self.main_workdir):
            return None
        return source


def _is_forbidden_path(path: Path) -> bool:
    parts = path.parts
    if any(part in _FORBIDDEN_DIR_PARTS for part in parts):
        return True
    name = path.name.lower()
    if name in _FORBIDDEN_FILENAMES:
        return True
    return any(token in name for token in _FORBIDDEN_SUBSTRINGS)


def _is_relative_to(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents
