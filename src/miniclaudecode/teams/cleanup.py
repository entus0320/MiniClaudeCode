"""AgentTeam cleanup helpers."""

from __future__ import annotations

from miniclaudecode.teams.core import TeamOperationResult


class TeamCleanup:
    """Explicit cleanup facade. First stage never silently deletes resources."""

    def cleanup(self, *, confirm: bool = False) -> TeamOperationResult:
        if not confirm:
            return TeamOperationResult(False, "团队清理涉及文件或目录整理，必须显式确认。", requires_confirmation=True)
        return TeamOperationResult(True, "团队清理已确认；第一阶段仅执行可观察状态整理，不自动删除工作目录。")
