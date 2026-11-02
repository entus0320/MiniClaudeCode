"""OpenAI and OpenAI-compatible provider implementation."""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from typing import Any

from miniclaudecode.config import ProviderConfig
from miniclaudecode.errors import safe_message
from miniclaudecode.prompting import AssembledPrompt
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

OPENAI_STREAM_OPTIONS = {"include_usage": True}
KNOWN_OPENAI_CONTEXT_WINDOWS = {
    "gpt-5": 400_000,
}


class OpenAIProvider(BaseProvider):
    """Stream chat completions through the OpenAI SDK."""

    def __init__(self, config: ProviderConfig, client: Any | None = None) -> None:
        self.config = config
        self._client = client

    def stream_chat(self, messages: Sequence[Message], prompt: AssembledPrompt | None = None) -> Iterator[str]:
        """Stream text deltas from OpenAI-compatible chat completions."""

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
        """Stream text and final tool calls from OpenAI-compatible chat completions."""

        text_parts: list[str] = []
        tool_accumulator: dict[int, dict[str, str]] = {}
        usage: ProviderUsage | None = None
        try:
            stream = self.client.chat.completions.create(**self._build_request(messages, tools, prompt))
            for chunk in stream:
                chunk_usage = _extract_usage(chunk)
                if chunk_usage is not None:
                    usage = chunk_usage
                content = _extract_delta_content(chunk)
                if content:
                    text_parts.append(content)
                    yield ProviderTextDelta(content)
                _accumulate_tool_calls(chunk, tool_accumulator)
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(self._safe_error(exc)) from exc

        yield ProviderTurnComplete(
            text="".join(text_parts),
            tool_calls=_build_tool_calls(tool_accumulator),
            usage=usage,
        )

    @property
    def context_window_tokens(self) -> int | None:
        """Return known OpenAI context windows for current MiniClaudeCode defaults."""

        if self.config.protocol != "openai":
            return None
        return KNOWN_OPENAI_CONTEXT_WINDOWS.get(self.config.model)

    def count_tokens(
        self,
        messages: Sequence[Message],
        tools: Sequence[Tool] | None = None,
        prompt: AssembledPrompt | None = None,
    ) -> int | None:
        """Count request input tokens when an OpenAI-compatible client exposes a counter."""

        completions = getattr(getattr(self.client, "chat", None), "completions", None)
        counter = getattr(completions, "count_tokens", None)
        if counter is None:
            return None
        try:
            request = self._build_request(messages, tools, prompt)
            request.pop("stream", None)
            request.pop("stream_options", None)
            result = counter(**request)
        except Exception:
            return None
        return _first_int_attr(result, "input_tokens", "prompt_tokens")

    @property
    def client(self) -> Any:
        """Return the injected client or lazily create an OpenAI SDK client."""

        if self._client is None:
            from openai import OpenAI

            kwargs: dict[str, str] = {"api_key": self.config.credential or ""}
            if self.config.base_url is not None:
                kwargs["base_url"] = self.config.base_url
            self._client = OpenAI(**kwargs)
        return self._client

    def _build_request(
        self,
        messages: Sequence[Message],
        tools: Sequence[Tool] | None = None,
        prompt: AssembledPrompt | None = None,
    ) -> dict[str, Any]:
        request: dict[str, Any] = {
            "model": self.config.model,
            "messages": to_openai_messages(messages, prompt=prompt),
            "stream": True,
        }
        if self.config.protocol == "openai":
            request["stream_options"] = OPENAI_STREAM_OPTIONS
        if tools:
            request["tools"] = to_openai_tools(tools)
        return request

    def _safe_error(self, exc: Exception) -> str:
        error_name = exc.__class__.__name__
        if error_name == "AuthenticationError":
            message = f"OpenAI 认证失败，请检查 {self.config.credential_env}."
        elif error_name in {"APIConnectionError", "APITimeoutError"}:
            message = "OpenAI 网络连接失败，请稍后重试。"
        elif error_name == "RateLimitError":
            message = "OpenAI 请求频率受限，请稍后重试。"
        elif error_name == "PermissionDeniedError":
            message = "OpenAI 权限不足，请检查账号或模型权限。"
        else:
            message = "OpenAI provider 调用失败。"
        return safe_message(message, secret=self.config.credential)


def to_openai_tools(tools: Sequence[Tool]) -> list[dict[str, Any]]:
    """Convert local tools to OpenAI Chat Completions tools."""

    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema,
            },
        }
        for tool in tools
    ]


def to_openai_messages(messages: Sequence[Message], prompt: AssembledPrompt | None = None) -> list[dict[str, Any]]:
    """Convert provider-neutral messages to OpenAI chat messages."""

    converted: list[dict[str, Any]] = []
    if prompt is not None and prompt.blocks:
        converted.append({"role": "system", "content": render_openai_prompt(prompt)})
    for message in messages:
        if message.role == "tool":
            converted.append(
                {
                    "role": "tool",
                    "tool_call_id": message.tool_call_id,
                    "content": message.content,
                }
            )
        elif message.role == "assistant" and message.tool_calls:
            converted.append(
                {
                    "role": "assistant",
                    "content": message.content or None,
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": call.raw_arguments or json.dumps(call.arguments, ensure_ascii=False),
                            },
                        }
                        for call in message.tool_calls
                    ],
                }
            )
        else:
            converted.append({"role": message.role, "content": message.content})
    return converted


def render_openai_prompt(prompt: AssembledPrompt) -> str:
    """Render assembled prompt blocks as one OpenAI-compatible system message."""

    return prompt.render_text()


def _extract_delta_content(chunk: Any) -> str | None:
    choices = getattr(chunk, "choices", None)
    if not choices:
        return None
    delta = getattr(choices[0], "delta", None)
    return getattr(delta, "content", None)


def _extract_usage(chunk: Any) -> ProviderUsage | None:
    usage = getattr(chunk, "usage", None)
    if usage is None:
        return None
    return ProviderUsage(
        input_tokens=_first_int_attr(usage, "input_tokens", "prompt_tokens"),
        output_tokens=_first_int_attr(usage, "output_tokens", "completion_tokens"),
    )


def _first_int_attr(item: Any, *names: str) -> int | None:
    for name in names:
        value = getattr(item, name, None)
        if isinstance(value, int):
            return value
    return None


def _accumulate_tool_calls(chunk: Any, accumulator: dict[int, dict[str, str]]) -> None:
    choices = getattr(chunk, "choices", None)
    if not choices:
        return
    delta = getattr(choices[0], "delta", None)
    tool_calls = getattr(delta, "tool_calls", None)
    if not tool_calls:
        return
    for call in tool_calls:
        index = getattr(call, "index", 0)
        current = accumulator.setdefault(index, {"id": "", "name": "", "arguments": ""})
        call_id = getattr(call, "id", None)
        if call_id:
            current["id"] = call_id
        function = getattr(call, "function", None)
        if function is None:
            continue
        name = getattr(function, "name", None)
        if name:
            current["name"] = name
        arguments = getattr(function, "arguments", None)
        if arguments:
            current["arguments"] += arguments


def _build_tool_calls(accumulator: dict[int, dict[str, str]]) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for index in sorted(accumulator):
        item = accumulator[index]
        raw_arguments = item["arguments"]
        try:
            parsed = json.loads(raw_arguments) if raw_arguments else {}
            parse_error = None
        except json.JSONDecodeError as exc:
            parsed = {}
            parse_error = f"工具参数 JSON 解析失败：{exc}"
        calls.append(
            ToolCall(
                id=item["id"],
                name=item["name"],
                arguments=parsed if isinstance(parsed, dict) else {},
                raw_arguments=raw_arguments,
                provider="openai",
                parse_error=parse_error,
            )
        )
    return calls
