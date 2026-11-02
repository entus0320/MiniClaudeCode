"""Persistent shared task board for AgentTeam."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from miniclaudecode.teams.core import SharedTask, SharedTaskStatus, TeamError


class SharedTaskBoard:
    """Create, list and update persistent team tasks."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def create(self, title: str, *, description: str = "", owner_member: str | None = None, dependencies: tuple[str, ...] = (), created_by: str | None = None) -> SharedTask:
        if not title.strip():
            raise TeamError("任务标题不能为空。")
        now = time.time()
        task = SharedTask(
            task_id=f"task-{uuid.uuid4().hex[:8]}",
            title=title.strip(),
            description=description,
            owner_member=owner_member,
            dependencies=dependencies,
            created_by=created_by,
            created_at=now,
            updated_at=now,
        )
        tasks = [*self.list_tasks(), task]
        self.save(tasks)
        return task

    def get(self, task_id: str) -> SharedTask:
        for task in self.list_tasks():
            if task.task_id == task_id:
                return task
        raise TeamError(f"共享任务不存在：{task_id}")

    def list_tasks(self) -> tuple[SharedTask, ...]:
        if not self.path.exists():
            return ()
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise TeamError("共享任务文件格式错误。")
        return tuple(_task_from_dict(item) for item in raw.get("tasks", []) if isinstance(item, dict))

    def update(self, task_id: str, *, status: str | None = None, owner_member: str | None = None, result_summary: str | None = None) -> SharedTask:
        tasks = list(self.list_tasks())
        for index, task in enumerate(tasks):
            if task.task_id != task_id:
                continue
            updated = task
            if status is not None:
                updated = replace(updated, status=SharedTaskStatus(status))
            if owner_member is not None:
                updated = replace(updated, owner_member=owner_member)
            if result_summary is not None:
                updated = replace(updated, result_summary=result_summary)
            updated = replace(updated, updated_at=time.time())
            tasks[index] = updated
            self.save(tasks)
            return updated
        raise TeamError(f"共享任务不存在：{task_id}")

    def save(self, tasks: list[SharedTask]) -> None:
        payload = {"schema_version": 1, "tasks": [_task_to_dict(task) for task in tasks]}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)


def _task_to_dict(task: SharedTask) -> dict[str, Any]:
    payload = asdict(task)
    payload["status"] = task.status.value
    payload["dependencies"] = list(task.dependencies)
    return payload


def _task_from_dict(raw: dict[str, Any]) -> SharedTask:
    return SharedTask(
        task_id=str(raw.get("task_id", "")),
        title=str(raw.get("title", "")),
        description=str(raw.get("description", "")),
        status=SharedTaskStatus(str(raw.get("status", SharedTaskStatus.TODO.value))),
        owner_member=str(raw["owner_member"]) if raw.get("owner_member") is not None else None,
        owner_agent_id=str(raw["owner_agent_id"]) if raw.get("owner_agent_id") is not None else None,
        dependencies=tuple(str(item) for item in raw.get("dependencies", [])),
        created_by=str(raw["created_by"]) if raw.get("created_by") is not None else None,
        created_at=float(raw.get("created_at", 0.0)),
        updated_at=float(raw.get("updated_at", 0.0)),
        result_summary=str(raw.get("result_summary", "")),
    )
