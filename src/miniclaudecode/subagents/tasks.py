"""Background task management for SubAgent runs."""

from __future__ import annotations

import itertools
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace

from miniclaudecode.subagents.core import (
    SubAgentLaunchMode,
    SubAgentNotification,
    SubAgentRunResult,
    SubAgentSpawnRequest,
    SubAgentStatus,
    SubAgentUsage,
)
from miniclaudecode.subagents.trace import TraceRegistry


@dataclass(frozen=True)
class TaskRecord:
    """Observable state for one background SubAgent task."""

    task_id: str
    request: SubAgentSpawnRequest
    launch_mode: SubAgentLaunchMode
    status: SubAgentStatus
    summary: str
    started_at: float | None = None
    ended_at: float | None = None
    result: str = ""
    error: str | None = None
    usage: SubAgentUsage = SubAgentUsage.empty()
    trace_id: str | None = None


class TaskManager:
    """Run SubAgents in background threads and publish notifications."""

    def __init__(self, *, trace_registry: TraceRegistry | None = None, default_timeout_seconds: float = 600.0) -> None:
        self.trace_registry = trace_registry
        self.default_timeout_seconds = default_timeout_seconds
        self._counter = itertools.count(1)
        self._records: dict[str, TaskRecord] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._notifications: list[SubAgentNotification] = []
        self._lock = threading.Lock()

    def start_background(
        self,
        request: SubAgentSpawnRequest,
        *,
        launch_mode: SubAgentLaunchMode,
        run_callable: Callable[[threading.Event, str | None, str], SubAgentRunResult],
        subagent_type: str | None = None,
    ) -> TaskRecord:
        """Start a background SubAgent task."""

        task_id = f"task-{next(self._counter)}"
        cancel_event = threading.Event()
        trace_id = None
        if self.trace_registry is not None:
            trace_id = self.trace_registry.start_child(
                task_id=task_id,
                launch_mode=launch_mode,
                subagent_type=subagent_type,
            )
        record = TaskRecord(
            task_id=task_id,
            request=request,
            launch_mode=launch_mode,
            status=SubAgentStatus.PENDING,
            summary=_summarize_task(request.task),
            trace_id=trace_id,
        )
        with self._lock:
            self._records[task_id] = record
            self._cancel_events[task_id] = cancel_event
        thread = threading.Thread(
            target=self._run_task,
            args=(task_id, cancel_event, trace_id, run_callable),
            daemon=True,
        )
        thread.start()
        return record

    def cancel(self, task_id: str) -> tuple[bool, str]:
        """Request cancellation for a background task."""

        with self._lock:
            record = self._records.get(task_id)
            if record is None:
                return False, f"未知后台任务：{task_id}"
            if record.status in {SubAgentStatus.COMPLETED, SubAgentStatus.FAILED, SubAgentStatus.CANCELLED, SubAgentStatus.TIMEOUT}:
                return False, f"任务 {task_id} 已结束，状态：{record.status.value}。"
            event = self._cancel_events.get(task_id)
            if event is not None:
                event.set()
        return True, f"已请求取消任务 {task_id}。"

    def list_tasks(self) -> list[TaskRecord]:
        with self._lock:
            return list(self._records.values())

    def get(self, task_id: str) -> TaskRecord | None:
        with self._lock:
            return self._records.get(task_id)

    def poll_notifications(self) -> list[SubAgentNotification]:
        with self._lock:
            notifications = list(self._notifications)
            self._notifications.clear()
        return notifications

    def _run_task(
        self,
        task_id: str,
        cancel_event: threading.Event,
        trace_id: str | None,
        run_callable: Callable[[threading.Event, str | None, str], SubAgentRunResult],
    ) -> None:
        self._update(task_id, status=SubAgentStatus.RUNNING, started_at=time.time())
        if self.trace_registry is not None:
            self.trace_registry.mark_status(trace_id, SubAgentStatus.RUNNING)
        try:
            result = run_callable(cancel_event, trace_id, task_id)
        except Exception as exc:
            result = SubAgentRunResult(success=False, status=SubAgentStatus.FAILED, error=str(exc), trace_id=trace_id, task_id=task_id)
        ended_at = time.time()
        status = result.status
        self._update(
            task_id,
            status=status,
            ended_at=ended_at,
            result=result.final_text,
            error=result.error,
            usage=result.usage,
        )
        if self.trace_registry is not None:
            self.trace_registry.mark_status(
                trace_id,
                status,
                usage=result.usage,
                error_summary=result.error,
                ended=True,
                task_id=task_id,
            )
        self._notify(task_id, result)

    def _update(
        self,
        task_id: str,
        *,
        status: SubAgentStatus | None = None,
        started_at: float | None = None,
        ended_at: float | None = None,
        result: str | None = None,
        error: str | None = None,
        usage: SubAgentUsage | None = None,
    ) -> None:
        with self._lock:
            record = self._records[task_id]
            self._records[task_id] = replace(
                record,
                status=status or record.status,
                started_at=started_at if started_at is not None else record.started_at,
                ended_at=ended_at if ended_at is not None else record.ended_at,
                result=result if result is not None else record.result,
                error=error if error is not None else record.error,
                usage=usage or record.usage,
            )

    def _notify(self, task_id: str, result: SubAgentRunResult) -> None:
        title = "后台子 Agent 已完成" if result.success else "后台子 Agent 未完成"
        message = f"- 摘要：{_summarize_task(result.to_summary())}\n- trace id：{result.trace_id or '无'}\n- 查看：/task info {task_id}"
        with self._lock:
            self._notifications.append(
                SubAgentNotification(
                    task_id=task_id,
                    status=result.status,
                    title=title,
                    message=message,
                )
            )


def _summarize_task(text: str) -> str:
    value = " ".join(text.strip().split())
    if len(value) <= 80:
        return value
    return value[:77] + "..."
