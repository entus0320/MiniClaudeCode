"""In-process AgentTeam backend."""

from __future__ import annotations

from miniclaudecode.teams.backends import BackendStartRequest
from miniclaudecode.teams.core import BackendDetection, BackendStartResult, TeamBackendKind, TeamMember, TeamMessage, TeamOperationResult
from miniclaudecode.teams.detect import detect_in_process


class InProcessBackend:
    kind = TeamBackendKind.IN_PROCESS

    def detect(self) -> BackendDetection:
        return detect_in_process()

    def start_member(self, request: BackendStartRequest) -> BackendStartResult:
        _ = request
        return BackendStartResult(True, "in_process 队员已准备运行。", {"backend": self.kind.value})

    def wake(self, member: TeamMember, message: TeamMessage) -> TeamOperationResult:
        return TeamOperationResult(True, f"已通过进程内调度唤醒队员 {member.name}。", metadata={"message_id": message.message_id})
