"""Persistent state store for managed worktrees."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from miniclaudecode.worktrees.core import (
    WorktreeDiagnostic,
    WorktreeError,
    WorktreeRecord,
    WorktreeState,
    WorktreeStatus,
    WorktreeTaskLink,
    WorktreeTaskStatus,
)

SCHEMA_VERSION = 1


class WorktreeStore:
    """Read and write managed worktree state as one JSON file."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self, *, mark_unrecoverable_tasks: bool = False) -> WorktreeState:
        if not self.path.exists():
            return WorktreeState()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise WorktreeError(f"Worktree 状态文件无法读取：{exc}") from exc
        if not isinstance(raw, dict):
            raise WorktreeError("Worktree 状态文件格式错误：根节点不是对象。")
        warnings: list[str] = []
        records = tuple(_record_from_dict(item) for item in raw.get("records", []) if isinstance(item, dict))
        checked_records = []
        for record in records:
            if record.path.exists():
                checked_records.append(record)
            else:
                checked_records.append(_replace_record_status(record, WorktreeStatus.MISSING))
                warnings.append(f"Worktree 目录不存在，已标记 missing：{record.name}")
        task_links = []
        for item in raw.get("task_links", []):
            if isinstance(item, dict):
                link = _task_link_from_dict(item)
                if mark_unrecoverable_tasks and link.status is WorktreeTaskStatus.RECORDED:
                    link = WorktreeTaskLink(link.task_id, link.worktree_name, WorktreeTaskStatus.UNRECOVERABLE, link.summary)
                task_links.append(link)
        active_name = raw.get("active_name") if isinstance(raw.get("active_name"), str) else None
        if active_name and not any(record.name == active_name and record.status is not WorktreeStatus.MISSING for record in checked_records):
            warnings.append(f"活跃 Worktree 无法恢复，已回到主工作目录：{active_name}")
            active_name = None
        return WorktreeState(records=tuple(checked_records), active_name=active_name, task_links=tuple(task_links), warnings=tuple(warnings))

    def save(self, state: WorktreeState) -> None:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "active_name": state.active_name,
            "records": [_record_to_dict(record) for record in state.records],
            "task_links": [_task_link_to_dict(link) for link in state.task_links],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)


def _record_to_dict(record: WorktreeRecord) -> dict[str, Any]:
    payload = asdict(record)
    payload["path"] = str(record.path)
    payload["status"] = record.status.value
    payload["diagnostics"] = [
        {"message": diagnostic.message, "path": str(diagnostic.path) if diagnostic.path is not None else None}
        for diagnostic in record.diagnostics
    ]
    return payload


def _record_from_dict(raw: dict[str, Any]) -> WorktreeRecord:
    diagnostics = tuple(
        WorktreeDiagnostic(str(item.get("message", "")), Path(item["path"]) if item.get("path") else None)
        for item in raw.get("diagnostics", [])
        if isinstance(item, dict)
    )
    return WorktreeRecord(
        name=str(raw.get("name", "")),
        path=Path(str(raw.get("path", ""))),
        branch=str(raw.get("branch", "")),
        status=WorktreeStatus(str(raw.get("status", WorktreeStatus.READY.value))),
        created_at=float(raw.get("created_at", 0.0)),
        updated_at=float(raw.get("updated_at", 0.0)),
        temporary=bool(raw.get("temporary", False)),
        head=str(raw["head"]) if raw.get("head") is not None else None,
        diagnostics=diagnostics,
        metadata=dict(raw.get("metadata", {})) if isinstance(raw.get("metadata", {}), dict) else {},
    )


def _replace_record_status(record: WorktreeRecord, status: WorktreeStatus) -> WorktreeRecord:
    return WorktreeRecord(
        name=record.name,
        path=record.path,
        branch=record.branch,
        status=status,
        created_at=record.created_at,
        updated_at=record.updated_at,
        temporary=record.temporary,
        head=record.head,
        diagnostics=record.diagnostics,
        metadata=record.metadata,
    )


def _task_link_to_dict(link: WorktreeTaskLink) -> dict[str, str]:
    return {
        "task_id": link.task_id,
        "worktree_name": link.worktree_name,
        "status": link.status.value,
        "summary": link.summary,
    }


def _task_link_from_dict(raw: dict[str, Any]) -> WorktreeTaskLink:
    return WorktreeTaskLink(
        task_id=str(raw.get("task_id", "")),
        worktree_name=str(raw.get("worktree_name", "")),
        status=WorktreeTaskStatus(str(raw.get("status", WorktreeTaskStatus.RECORDED.value))),
        summary=str(raw.get("summary", "")),
    )
