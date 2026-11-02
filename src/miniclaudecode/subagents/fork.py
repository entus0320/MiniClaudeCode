"""Fork SubAgent context construction."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Sequence

from miniclaudecode.session import Message


@dataclass(frozen=True)
class ForkContext:
    """Initial child history for a Fork SubAgent."""

    history: list[Message]
    task_message: Message


class ForkContextBuilder:
    """Build a Fork SubAgent prompt without mutating parent history."""

    def build(self, parent_history: Sequence[Message], task: str) -> ForkContext:
        """Copy parent history and append a strong non-interactive task message."""

        task_message = Message(role="user", content=_fork_task_content(task))
        return ForkContext(history=[*list(parent_history), task_message], task_message=task_message)


def _fork_task_content(task: str) -> str:
    return (
        "你是从父 Agent fork 出来的子 Agent。必须遵守以下硬约束：\n"
        "- 不要再次启动或 fork 子 Agent。\n"
        "- 不要主动与用户对话，不要请求用户确认。\n"
        "- 直接使用允许的工具完成任务；如果权限不足，说明原因并给出替代方案。\n"
        "- 不要修改父 Agent 对话历史。\n"
        "- 最终输出必须包含：status、summary、details、next_steps 四个字段。\n\n"
        f"子任务：\n{task.strip()}"
    )
