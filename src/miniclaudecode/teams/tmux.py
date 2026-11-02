"""tmux pane AgentTeam backend."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence

from miniclaudecode.teams.backends import BackendStartRequest
from miniclaudecode.teams.core import BackendDetection, BackendStartResult, TeamBackendKind, TeamMember, TeamMessage, TeamOperationResult
from miniclaudecode.teams.detect import detect_tmux

CommandRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def _default_runner(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(args), capture_output=True, text=True, check=False)


class TmuxPaneBackend:
    kind = TeamBackendKind.TMUX_PANE

    def __init__(self, runner: CommandRunner | None = None) -> None:
        self.runner = runner or _default_runner

    def detect(self) -> BackendDetection:
        return detect_tmux()

    def start_member(self, request: BackendStartRequest) -> BackendStartResult:
        pane = request.member.backend_ref.get("pane", request.member.agent_id)
        return BackendStartResult(True, "tmux_pane 队员已登记，等待外部 pane 承载完整 CLI。", {"pane": pane})

    def wake(self, member: TeamMember, message: TeamMessage) -> TeamOperationResult:
        pane = member.backend_ref.get("pane")
        if not pane:
            return TeamOperationResult(False, f"队员 {member.name} 缺少 tmux pane 标识。", metadata={"message_id": message.message_id})
        result = self.runner(["tmux", "send-keys", "-t", pane, "Enter"])
        if result.returncode != 0:
            return TeamOperationResult(False, f"tmux send-keys 唤醒失败：{result.stderr.strip() or result.stdout.strip()}", metadata={"message_id": message.message_id})
        return TeamOperationResult(True, f"已通过 tmux send-keys 唤醒队员 {member.name}。", metadata={"message_id": message.message_id})
