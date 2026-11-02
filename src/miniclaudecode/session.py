"""In-memory conversation session for MiniClaudeCode."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from miniclaudecode.tools.base import ToolCall, ToolResult

SUPPORTED_MESSAGE_ROLES = frozenset({"user", "assistant", "tool"})


@dataclass(frozen=True)
class ToolResultOffloadInfo:
    """Metadata for one tool result saved as a local artifact."""

    tool_name: str
    tool_call_id: str | None
    path: str
    bytes: int
    sha256: str
    reason: str
    original_chars: int
    slimmed_chars: int
    released_chars: int
    original_estimated_tokens: int
    slimmed_estimated_tokens: int
    released_estimated_tokens: int
    content_preview: str
    error_preview: str | None = None


@dataclass(frozen=True)
class SlimmedToolResult:
    """A model-facing tool result plus optional offload metadata."""

    content: str
    offload: ToolResultOffloadInfo | None = None


class ToolResultSlimmerProtocol(Protocol):
    def slim_result(self, result: ToolResult) -> str: ...

    def slim_results(self, results: Sequence[ToolResult]) -> list[str]: ...

    def slim_results_detailed(self, results: Sequence[ToolResult]) -> list[SlimmedToolResult]: ...


class SessionRecorderProtocol(Protocol):
    def record_message(self, message: "Message") -> None: ...

    def record_history_replaced(self, messages: Sequence["Message"]) -> None: ...


@dataclass(frozen=True)
class Message:
    """A provider-neutral message in the current conversation."""

    role: str
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    tool_name: str | None = None
    synthetic_kind: str | None = None
    tool_result_offload: ToolResultOffloadInfo | None = None

    def __post_init__(self) -> None:
        if self.role not in SUPPORTED_MESSAGE_ROLES:
            supported = ", ".join(sorted(SUPPORTED_MESSAGE_ROLES))
            raise ValueError(f"Message role must be one of: {supported}.")
        if not isinstance(self.content, str):
            raise TypeError("Message content must be a string.")
        if self.role == "tool" and not self.tool_call_id:
            raise ValueError("Tool messages require a tool_call_id.")


class Session:
    """Maintain conversation history for the current process."""

    def __init__(
        self,
        tool_result_slimmer: ToolResultSlimmerProtocol | None = None,
        recorder: SessionRecorderProtocol | None = None,
    ) -> None:
        self._history: list[Message] = []
        self.tool_result_slimmer = tool_result_slimmer
        self.recorder = recorder

    def add_user_message(self, content: str) -> Message:
        """Append a user message to the conversation history."""

        return self._append_message("user", content)

    def add_assistant_message(self, content: str) -> Message:
        """Append a completed assistant message to the conversation history."""

        return self._append_message("assistant", content)

    def add_assistant_tool_calls(self, content: str, tool_calls: list[ToolCall]) -> Message:
        """Append an assistant message that requested local tools."""

        message = Message(role="assistant", content=content, tool_calls=list(tool_calls))
        self._history.append(message)
        self._record_message(message)
        return message

    def add_tool_result(self, result: ToolResult) -> Message:
        """Append one tool result to the conversation history."""

        if self.tool_result_slimmer is not None and hasattr(self.tool_result_slimmer, "slim_results_detailed"):
            slimmed = self.tool_result_slimmer.slim_results_detailed([result])[0]
        else:
            content = (
                self.tool_result_slimmer.slim_result(result)
                if self.tool_result_slimmer is not None
                else result.to_model_text()
            )
            slimmed = SlimmedToolResult(content=content)
        message = self._tool_message(result, slimmed.content, tool_result_offload=slimmed.offload)
        self._history.append(message)
        self._record_message(message)
        return message

    def add_tool_results(self, results: list[ToolResult]) -> list[Message]:
        """Append multiple tool results to the conversation history."""

        if self.tool_result_slimmer is not None and hasattr(self.tool_result_slimmer, "slim_results_detailed"):
            slimmed = self.tool_result_slimmer.slim_results_detailed(results)
        else:
            contents = (
                self.tool_result_slimmer.slim_results(results)
                if self.tool_result_slimmer is not None
                else [result.to_model_text() for result in results]
            )
            slimmed = [SlimmedToolResult(content=content) for content in contents]
        messages = [
            self._tool_message(result, item.content, tool_result_offload=item.offload)
            for result, item in zip(results, slimmed, strict=True)
        ]
        self._history.extend(messages)
        for message in messages:
            self._record_message(message)
        return messages

    def get_history(self) -> list[Message]:
        """Return a snapshot of the current conversation history."""

        return list(self._history)

    def clear(self) -> None:
        """Clear the in-memory conversation history."""

        self._history.clear()

    def replace_history(self, messages: Sequence[Message]) -> None:
        """Replace in-memory history with a validated message sequence."""

        self._history = list(messages)
        if self.recorder is not None:
            self.recorder.record_history_replaced(self._history)

    def save(self, path: str | Path) -> None:
        """Reserved for future history persistence behavior."""

        raise NotImplementedError("Session.save is reserved for a later task.")

    def compress(self) -> None:
        """Reserved for caller-provided context compression orchestration."""

        raise NotImplementedError("Session.compress requires a context manager in the caller.")

    def _append_message(self, role: str, content: str) -> Message:
        message = Message(role=role, content=content)
        self._history.append(message)
        self._record_message(message)
        return message

    def _record_message(self, message: Message) -> None:
        if self.recorder is not None:
            self.recorder.record_message(message)

    def _tool_message(
        self,
        result: ToolResult,
        content: str,
        *,
        tool_result_offload: ToolResultOffloadInfo | None = None,
    ) -> Message:
        return Message(
            role="tool",
            content=content,
            tool_call_id=result.tool_call_id,
            tool_name=result.tool_name,
            tool_result_offload=tool_result_offload,
        )
