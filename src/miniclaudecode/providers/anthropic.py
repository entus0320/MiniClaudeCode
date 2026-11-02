"""Anthropic provider implementation."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any

from miniclaudecode.config import ProviderConfig
from miniclaudecode.errors import safe_message
from miniclaudecode.prompting import AssembledPrompt, PromptBlock
from miniclaudecode.providers.base import (
    BaseProvider,
    ProviderError,
    ProviderEvent,
    ProviderTextDelta,
    ProviderTurnComplete,
    ProviderUsage,
)
from miniclaudecode.session import Message
from miniclaudecode.tools.base import Tool, ToolCall

DEFAULT_MAX_TOKENS = 4096


class AnthropicProvider(BaseProvider):
    """Stream chat completions from Claude through the Anthropic SDK."""

    def __init__(self, config: ProviderConfig, client: Any | None = None) -> None:
        self.config = config
        self._client = client

    def stream_chat(self, messages: Sequence[Message], prompt: AssembledPrompt | None = None) -> Iterator[str]:
        """Stream text deltas from Anthropic for the given message history."""

        stream = self.stream_turn(messages, prompt=prompt) if prompt is not None else self.stream_turn(messages)
        for event in stream:
            if isinstance(event, ProviderTextDelta):
                yield event.text

    def stream_turn(
        self,
        messages: Sequence[Message],
        tools: Sequence[Tool] | None = None,
        prompt: AssembledPrompt | None = None,
    ) -> Iterator[ProviderEvent]:
        """Stream text and final tool calls from Anthropic."""

        text_parts: list[str] = []
        try:
            with self.client.messages.stream(**self._build_request(messages, tools, prompt)) as stream:
                if hasattr(stream, "__iter__"):
                    for event in stream:
                        text = _extract_text_delta(event)
                        if text:
                            text_parts.append(text)
                            yield ProviderTextDelta(text)
                else:
                    for text in stream.text_stream:
                        if text:
                            text_parts.append(text)
                            yield ProviderTextDelta(text)
                final_message = stream.get_final_message() if hasattr(stream, "get_final_message") else None
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(self._safe_error(exc)) from exc

        yield ProviderTurnComplete(
            text="".join(text_parts) or _extract_message_text(final_message),
            tool_calls=_extract_tool_calls(final_message),
            stop_reason=getattr(final_message, "stop_reason", None),
            usage=_extract_usage(final_message),
        )

    @property
    def context_window_tokens(self) -> int | None:
        """Return known Claude context windows for current MiniClaudeCode defaults."""

        if self.config.model in {"claude-fable-5", "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-4-6"}:
            return 1_000_000
        if self.config.model == "claude-haiku-4-5":
            return 200_000
        return None

    def count_tokens(
        self,
        messages: Sequence[Message],
        tools: Sequence[Tool] | None = None,
        prompt: AssembledPrompt | None = None,
    ) -> int | None:
        """Count Anthropic request input tokens when the SDK exposes count_tokens."""

        try:
            request = self._build_request(messages, tools, prompt)
            request.pop("max_tokens", None)
            result = self.client.messages.count_tokens(**request)
        except Exception:
            return None
        return getattr(result, "input_tokens", None)

    @property
    def client(self) -> Any:
        """Return the injected client or lazily create an Anthropic SDK client."""

        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic(api_key=self.config.credential)
        return self._client

    def _build_request(
        self,
        messages: Sequence[Message],
        tools: Sequence[Tool] | None = None,
        prompt: AssembledPrompt | None = None,
    ) -> dict[str, Any]:
        request: dict[str, Any] = {
            "model": self.config.model,
            "max_tokens": DEFAULT_MAX_TOKENS,
            "messages": to_anthropic_messages(messages),
        }
        if prompt is not None and prompt.blocks:
            request["system"] = to_anthropic_system(prompt)
        if tools:
            request["tools"] = to_anthropic_tools(tools)
        if self.config.thinking.enabled:
            request["thinking"] = {
                "type": "adaptive",
                "display": self.config.thinking.display,
            }
            request["output_config"] = {"effort": self.config.thinking.effort}
        return request

    def _safe_error(self, exc: Exception) -> str:
        error_name = exc.__class__.__name__
        if error_name == "AuthenticationError":
            message = f"Anthropic 认证失败，请检查 {self.config.credential_env}."
        elif error_name in {"APIConnectionError", "APITimeoutError"}:
            message = "Anthropic 网络连接失败，请稍后重试。"
        elif error_name == "RateLimitError":
            message = "Anthropic 请求频率受限，请稍后重试。"
        elif error_name == "PermissionDeniedError":
            message = "Anthropic 权限不足，请检查账号或模型权限。"
        else:
            message = "Anthropic provider 调用失败。"
        return safe_message(message, secret=self.config.credential)


def to_anthropic_system(prompt: AssembledPrompt) -> list[dict[str, Any]]:
    """Convert assembled prompt blocks to Claude Messages API system blocks."""

    blocks = list(prompt.blocks)
    cacheable_indices = [index for index, block in enumerate(blocks) if block.cache_policy.cacheable]
    cache_breakpoint = cacheable_indices[-1] if cacheable_indices else None
    converted: list[dict[str, Any]] = []
    for index, block in enumerate(blocks):
        item: dict[str, Any] = {"type": "text", "text": _render_system_block(block)}
        if index == cache_breakpoint:
            item["cache_control"] = {"type": "ephemeral"}
        converted.append(item)
    return converted


def to_anthropic_tools(tools: Sequence[Tool]) -> list[dict[str, Any]]:
    """Convert local tools to Claude Messages API tool definitions."""

    converted: list[dict[str, Any]] = []
    for tool in tools:
        converted.append(
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
        )
    if converted:
        converted[-1]["cache_control"] = {"type": "ephemeral"}
    return converted


def to_anthropic_messages(messages: Sequence[Message]) -> list[dict[str, Any]]:
    """Convert provider-neutral messages to Claude Messages API input."""

    converted: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "tool":
            converted.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": message.tool_call_id,
                            "content": message.content,
                        }
                    ],
                }
            )
        elif message.role == "assistant" and message.tool_calls:
            content: list[dict[str, Any]] = []
            if message.content:
                content.append({"type": "text", "text": message.content})
            for call in message.tool_calls:
                content.append(
                    {
                        "type": "tool_use",
                        "id": call.id,
                        "name": call.name,
                        "input": call.arguments,
                    }
                )
            converted.append({"role": "assistant", "content": content})
        else:
            converted.append({"role": message.role, "content": message.content})
    return converted


def _render_system_block(block: PromptBlock) -> str:
    return block.render()


def _extract_usage(message: Any) -> ProviderUsage | None:
    usage = getattr(message, "usage", None)
    if usage is None:
        return None
    return ProviderUsage(
        input_tokens=getattr(usage, "input_tokens", None),
        output_tokens=getattr(usage, "output_tokens", None),
        cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", None),
        cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", None),
    )


def _extract_text_delta(event: Any) -> str | None:
    if getattr(event, "type", None) != "content_block_delta":
        return None
    delta = getattr(event, "delta", None)
    if getattr(delta, "type", None) == "text_delta":
        return getattr(delta, "text", None)
    return None


def _extract_message_text(message: Any) -> str:
    parts: list[str] = []
    for block in getattr(message, "content", []) or []:
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", ""))
    return "".join(parts)


def _extract_tool_calls(message: Any) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for block in getattr(message, "content", []) or []:
        if getattr(block, "type", None) == "tool_use":
            tool_input = getattr(block, "input", {})
            calls.append(
                ToolCall(
                    id=getattr(block, "id", ""),
                    name=getattr(block, "name", ""),
                    arguments=tool_input if isinstance(tool_input, dict) else {},
                    provider="anthropic",
                )
            )
    return calls
