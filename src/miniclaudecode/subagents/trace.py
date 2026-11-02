"""In-memory parent/child trace registry for SubAgent runs."""

from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, replace

from miniclaudecode.subagents.core import SubAgentLaunchMode, SubAgentStatus, SubAgentUsage


@dataclass(frozen=True)
class TraceRecord:
    """One SubAgent execution trace."""

    trace_id: str
    parent_trace_id: str | None
    task_id: str | None
    launch_mode: SubAgentLaunchMode
    subagent_type: str | None
    status: SubAgentStatus
    usage: SubAgentUsage
    started_at: float
    ended_at: float | None = None
    error_summary: str | None = None


class TraceRegistry:
    """Keep in-memory SubAgent trace records for the current process."""

    def __init__(self) -> None:
        self._counter = itertools.count(1)
        self._records: dict[str, TraceRecord] = {}

    def start_child(
        self,
        *,
        parent_trace_id: str | None = None,
        task_id: str | None = None,
        launch_mode: SubAgentLaunchMode,
        subagent_type: str | None,
    ) -> str:
        trace_id = f"trace-{next(self._counter)}"
        self._records[trace_id] = TraceRecord(
            trace_id=trace_id,
            parent_trace_id=parent_trace_id,
            task_id=task_id,
            launch_mode=launch_mode,
            subagent_type=subagent_type,
            status=SubAgentStatus.PENDING,
            usage=SubAgentUsage.empty(),
            started_at=time.time(),
        )
        return trace_id

    def mark_status(
        self,
        trace_id: str | None,
        status: SubAgentStatus,
        *,
        usage: SubAgentUsage | None = None,
        error_summary: str | None = None,
        ended: bool = False,
        task_id: str | None = None,
    ) -> None:
        if trace_id is None or trace_id not in self._records:
            return
        record = self._records[trace_id]
        self._records[trace_id] = replace(
            record,
            status=status,
            usage=usage or record.usage,
            error_summary=error_summary if error_summary is not None else record.error_summary,
            ended_at=time.time() if ended else record.ended_at,
            task_id=task_id if task_id is not None else record.task_id,
        )

    def get(self, trace_id: str) -> TraceRecord | None:
        return self._records.get(trace_id)

    def for_task(self, task_id: str) -> TraceRecord | None:
        for record in self._records.values():
            if record.task_id == task_id:
                return record
        return None

    def list_records(self) -> list[TraceRecord]:
        return list(self._records.values())
