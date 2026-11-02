"""Lifecycle manager for Git worktree isolation."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from miniclaudecode.worktrees.core import WorktreeConfig, WorktreeDiagnostic, WorktreeError, WorktreeOperationResult, WorktreeRecord, WorktreeState, WorktreeStatus
from miniclaudecode.worktrees.environment import EnvironmentInitializer
from miniclaudecode.worktrees.git import WorktreeGitClient
from miniclaudecode.worktrees.names import branch_name_for_worktree, validate_worktree_name
from miniclaudecode.worktrees.paths import WorktreePathResolver
from miniclaudecode.worktrees.protection import WorktreeProtection
from miniclaudecode.worktrees.store import WorktreeStore

WorkdirSwitchCallback = Callable[[Path], None]


class WorktreeManager:
    """Create, enter, exit, delete, and list managed worktrees."""

    def __init__(
        self,
        *,
        main_workdir: str | Path,
        config: WorktreeConfig,
        store: WorktreeStore,
        git_client: WorktreeGitClient | None = None,
        environment: EnvironmentInitializer | None = None,
        protection: WorktreeProtection | None = None,
        switch_callback: WorkdirSwitchCallback | None = None,
        state: WorktreeState | None = None,
    ) -> None:
        self.main_workdir = Path(main_workdir).resolve()
        self.config = config
        self.store = store
        self.resolver = WorktreePathResolver(self.main_workdir, config)
        self.git_client = git_client or WorktreeGitClient(self.main_workdir)
        self.environment = environment or EnvironmentInitializer(self.main_workdir, config)
        self.protection = protection or WorktreeProtection(self.git_client)
        self._switch_callback = switch_callback
        self._state = state or store.load()
        self._persist()

    @property
    def state(self) -> WorktreeState:
        return self._state

    @property
    def active_name(self) -> str | None:
        return self._state.active_name

    def set_switch_callback(self, callback: WorkdirSwitchCallback | None) -> None:
        self._switch_callback = callback

    def list_records(self) -> list[WorktreeRecord]:
        return sorted(self._state.records, key=lambda record: record.name)

    def get(self, name: str) -> WorktreeRecord | None:
        normalized = validate_worktree_name(name, self.config)
        for record in self._state.records:
            if record.name == normalized:
                return record
        return None

    def create(self, name: str, *, base_ref: str | None = None, temporary: bool = False) -> WorktreeOperationResult:
        try:
            normalized = validate_worktree_name(name, self.config)
            path = self.resolver.resolve(normalized)
            existing = self.get(normalized)
            if existing is not None and path.exists():
                return self.recover(normalized)
            branch = branch_name_for_worktree(normalized, self.config)
            if path.exists():
                return self.recover(normalized)
            self.git_client.add_worktree(path, branch, base_ref=base_ref)
            diagnostics = self.environment.initialize(path)
            now = time.time()
            head = _safe_head(self.git_client, path)
            record = WorktreeRecord(
                name=normalized,
                path=path,
                branch=branch,
                status=WorktreeStatus.READY,
                created_at=now,
                updated_at=now,
                temporary=temporary,
                head=head,
                diagnostics=diagnostics,
            )
            self._upsert(record)
            return WorktreeOperationResult(True, f"已创建 Worktree：{normalized}\n- 路径：{path}\n- 分支：{branch}", record, diagnostics)
        except Exception as exc:
            return WorktreeOperationResult(False, f"创建 Worktree 失败：{exc}")

    def recover(self, name: str) -> WorktreeOperationResult:
        try:
            normalized = validate_worktree_name(name, self.config)
            path = self.resolver.resolve(normalized)
            if not path.exists():
                raise WorktreeError(f"Worktree 目录不存在：{path}")
            branch = _safe_branch(self.git_client, path) or branch_name_for_worktree(normalized, self.config)
            head = _safe_head(self.git_client, path)
            now = time.time()
            existing = self.get(normalized)
            record = WorktreeRecord(
                name=normalized,
                path=path,
                branch=branch,
                status=WorktreeStatus.READY,
                created_at=existing.created_at if existing is not None else now,
                updated_at=now,
                temporary=existing.temporary if existing is not None else False,
                head=head,
                diagnostics=existing.diagnostics if existing is not None else (),
                metadata=existing.metadata if existing is not None else {},
            )
            self._upsert(record)
            return WorktreeOperationResult(True, f"已快速恢复 Worktree：{normalized}\n- 路径：{path}\n- 分支：{branch}", record)
        except Exception as exc:
            return WorktreeOperationResult(False, f"恢复 Worktree 失败：{exc}")

    def enter(self, name: str) -> WorktreeOperationResult:
        try:
            record = self.get(name)
            if record is None:
                raise WorktreeError(f"未知 Worktree：{name}")
            if not record.path.exists():
                missing = replace(record, status=WorktreeStatus.MISSING, updated_at=time.time())
                self._upsert(missing)
                raise WorktreeError(f"Worktree 目录不存在：{record.path}")
            self._state = WorktreeState(records=self._state.records, active_name=record.name, task_links=self._state.task_links, warnings=self._state.warnings)
            active = replace(record, status=WorktreeStatus.ACTIVE, updated_at=time.time())
            self._upsert(active)
            if self._switch_callback is not None:
                self._switch_callback(record.path)
            self._persist()
            return WorktreeOperationResult(True, f"已进入 Worktree：{record.name}\n- 路径：{record.path}\n- 分支：{record.branch}", active)
        except Exception as exc:
            return WorktreeOperationResult(False, f"进入 Worktree 失败：{exc}")

    def exit(self) -> WorktreeOperationResult:
        active_name = self._state.active_name
        if active_name is None:
            return WorktreeOperationResult(True, "当前不在 Worktree 中。")
        record = self.get(active_name)
        if record is not None:
            self._upsert(replace(record, status=WorktreeStatus.READY, updated_at=time.time()))
        self._state = WorktreeState(records=self._state.records, active_name=None, task_links=self._state.task_links, warnings=self._state.warnings)
        if self._switch_callback is not None:
            self._switch_callback(self.main_workdir)
        self._persist()
        return WorktreeOperationResult(True, f"已退出 Worktree，回到主工作目录：{self.main_workdir}", record)

    def remove(self, name: str, *, confirm_discard: bool = False) -> WorktreeOperationResult:
        return self.delete(name, confirm_discard=confirm_discard)

    def delete(self, name: str, *, confirm_discard: bool = False) -> WorktreeOperationResult:
        try:
            record = self.get(name)
            if record is None:
                raise WorktreeError(f"未知 Worktree：{name}")
            if self._state.active_name == record.name:
                raise WorktreeError("不能删除当前正在使用的 Worktree，请先执行 /worktree exit。")
            protection = self.protection.check(record.path)
            if protection.requires_confirmation and not confirm_discard:
                return WorktreeOperationResult(
                    False,
                    f"拒绝删除 Worktree：{protection.reason}\n如需丢弃，请显式使用 --confirm-discard。",
                    record,
                    requires_confirmation=True,
                )
            self.git_client.remove_worktree(record.path, force=confirm_discard)
            deleted = replace(record, status=WorktreeStatus.DELETED, updated_at=time.time())
            records = tuple(item for item in self._state.records if item.name != record.name)
            self._state = WorktreeState(records=records, active_name=self._state.active_name, task_links=self._state.task_links, warnings=self._state.warnings)
            self._persist()
            return WorktreeOperationResult(True, f"已删除 Worktree：{record.name}", deleted)
        except Exception as exc:
            return WorktreeOperationResult(False, f"删除 Worktree 失败：{exc}")

    def auto_cleanup(self, *, now: float | None = None) -> tuple[WorktreeDiagnostic, ...]:
        """Clean expired temporary worktrees using fail-closed protection."""

        current = time.time() if now is None else now
        diagnostics: list[WorktreeDiagnostic] = []
        for record in self.list_records():
            if not record.temporary:
                continue
            if self._state.active_name == record.name:
                continue
            if current - record.updated_at < self.config.temporary_ttl_seconds:
                continue
            result = self.remove(record.name, confirm_discard=False)
            if result.success:
                diagnostics.append(WorktreeDiagnostic(f"AutoCleanup 已清理过期临时 Worktree：{record.name}", record.path))
            elif result.requires_confirmation:
                diagnostics.append(WorktreeDiagnostic(f"AutoCleanup 跳过需确认的 Worktree：{record.name}；{result.message}", record.path))
            else:
                diagnostics.append(WorktreeDiagnostic(f"AutoCleanup 跳过 Worktree：{record.name}；{result.message}", record.path))
        return tuple(diagnostics)

    def stale_cleanup(self) -> tuple[WorktreeDiagnostic, ...]:
        """Mark stale records whose directories no longer exist."""

        diagnostics: list[WorktreeDiagnostic] = []
        for record in self.list_records():
            if record.status is WorktreeStatus.DELETED:
                continue
            if record.path.exists():
                continue
            missing = replace(record, status=WorktreeStatus.MISSING, updated_at=time.time())
            self._upsert(missing)
            diagnostics.append(WorktreeDiagnostic(f"StaleCleanup 已标记 missing：{record.name}", record.path))
        return tuple(diagnostics)

    def status_text(self, name: str | None = None) -> str:
        if name is None:
            active = self._state.active_name or "主工作目录"
            lines = [f"当前 Worktree：{active}", f"主工作目录：{self.main_workdir}"]
            if not self._state.records:
                lines.append("暂无托管 Worktree。")
            return "\n".join(lines)
        record = self.get(name)
        if record is None:
            return f"未知 Worktree：{name}"
        protection = self.protection.check(record.path)
        return (
            f"Worktree：{record.name}\n"
            f"- 状态：{record.status.value}\n"
            f"- 路径：{record.path}\n"
            f"- 分支：{record.branch}\n"
            f"- 临时：{record.temporary}\n"
            f"- 保护检查：{protection.reason}"
        )

    def _upsert(self, record: WorktreeRecord) -> None:
        records = [item for item in self._state.records if item.name != record.name]
        records.append(record)
        self._state = WorktreeState(records=tuple(records), active_name=self._state.active_name, task_links=self._state.task_links, warnings=self._state.warnings)
        self._persist()

    def _persist(self) -> None:
        self.store.save(self._state)


def _safe_branch(git_client: WorktreeGitClient, path: Path) -> str | None:
    try:
        return git_client.current_branch(path)
    except Exception:
        return None


def _safe_head(git_client: WorktreeGitClient, path: Path) -> str | None:
    try:
        return git_client.current_head(path)
    except Exception:
        return None
