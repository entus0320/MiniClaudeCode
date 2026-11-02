"""iTerm2 pane AgentTeam backend."""

from __future__ import annotations

from collections.abc import Callable

from miniclaudecode.teams.backends import BackendStartRequest
from miniclaudecode.teams.core import BackendDetection, BackendStartResult, TeamBackendKind, TeamMember, TeamMessage, TeamOperationResult
from miniclaudecode.teams.detect import detect_iterm2

WakeAutomation = Callable[[str], tuple[bool, str]]


def _default_wake(target: str) -> tuple[bool, str]:
    _ = target
    return False, "iTerm2 自动化尚未配置。"


class ITerm2PaneBackend:
    kind = TeamBackendKind.ITERM2_PANE

    def __init__(self, wake_automation: WakeAutomation | None = None) -> None:
        self.wake_automation = wake_automation or _default_wake

    def detect(self) -> BackendDetection:
        return detect_iterm2()

    def start_member(self, request: BackendStartRequest) -> BackendStartResult:
        pane = request.member.backend_ref.get("pane", request.member.agent_id)
        return BackendStartResult(True, "iterm2_pane 队员已登记，等待外部 pane 承载完整 CLI。", {"pane": pane})

    def wake(self, member: TeamMember, message: TeamMessage) -> TeamOperationResult:
        pane = member.backend_ref.get("pane")
        if not pane:
            return TeamOperationResult(False, f"队员 {member.name} 缺少 iTerm2 pane 标识。", metadata={"message_id": message.message_id})
        ok, detail = self.wake_automation(pane)
        if not ok:
            return TeamOperationResult(False, f"iTerm2 pane 唤醒失败：{detail}", metadata={"message_id": message.message_id})
        return TeamOperationResult(True, f"已通过 iTerm2 自动化唤醒队员 {member.name}。", metadata={"message_id": message.message_id})
