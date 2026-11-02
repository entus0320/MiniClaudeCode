"""Base provider abstractions for MiniClaudeCode."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field

from miniclaudecode.prompting import AssembledPrompt
from miniclaudecode.session import Message
from miniclaudecode.tools.base import Tool, ToolCall


class ProviderError(Exception):
    """Raised when a provider cannot complete a chat request safely."""


@dataclass(frozen=True)
class ProviderTextDelta:
    """A streamed text delta from a provider."""

    text: str


@dataclass(frozen=True)
class ProviderUsage:
    """Provider-normalized token usage for one completed turn."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    cache_read_input_tokens: int | None = None


@dataclass(frozen=True)
class ProviderTurnComplete:
    """The final structured result for one provider turn."""

    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str | None = None
    usage: ProviderUsage | None = None


ProviderEvent = ProviderTextDelta | ProviderTurnComplete


class BaseProvider(ABC):
    """Minimal streaming chat interface shared by all providers."""

    @abstractmethod
    def stream_chat(self, messages: Sequence[Message], prompt: AssembledPrompt | None = None) -> Iterator[str]:
        """Stream text deltas for the given conversation history."""

    @property
    def context_window_tokens(self) -> int | None:
        """Return this provider/model context window if known."""

        return None

    def count_tokens(
        self,
        messages: Sequence[Message],
        tools: Sequence[Tool] | None = None,
        prompt: AssembledPrompt | None = None,
    ) -> int | None:
        """Return request input tokens if this provider can count them cheaply."""

        _ = messages, tools, prompt
        return None

    def stream_turn(
        self,
        messages: Sequence[Message],
        tools: Sequence[Tool] | None = None,
        prompt: AssembledPrompt | None = None,
    ) -> Iterator[ProviderEvent]:
        """Stream structured provider events for a single turn."""

        _ = tools
        text_parts: list[str] = []
        stream = self.stream_chat(messages, prompt=prompt) if prompt is not None else self.stream_chat(messages)
        for delta in stream:
            text_parts.append(delta)
            yield ProviderTextDelta(delta)
        yield ProviderTurnComplete(text="".join(text_parts))
