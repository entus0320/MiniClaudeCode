"""Provider-neutral tool abstractions for MiniClaudeCode."""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_SECRET_PATTERNS = (
    re.compile(r"sk-ant-[A-Za-z0-9_-]+"),
    re.compile(r"sk-proj-[A-Za-z0-9_-]+"),
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
)


@dataclass(frozen=True)
class ToolContext:
    """Runtime context shared by local tools."""

    workdir: Path
    command_timeout_seconds: float = 10.0
    max_read_bytes: int = 200_000
    max_output_chars: int = 200_000
    max_results: int = 200
    secrets: Sequence[str] = field(default_factory=tuple, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workdir", Path(self.workdir).resolve())


@dataclass(frozen=True)
class ToolCall:
    """A provider-neutral tool call requested by a model."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    raw_arguments: str | None = None
    provider: str | None = None
    parse_error: str | None = None


@dataclass(frozen=True)
class ToolResult:
    """A provider-neutral structured result from a tool execution."""

    tool_name: str
    success: bool
    content: str = ""
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    tool_call_id: str | None = None

    @property
    def is_error(self) -> bool:
        return not self.success

    def to_model_text(self) -> str:
        """Return a compact text representation suitable for model context."""

        payload: dict[str, Any] = {
            "tool": self.tool_name,
            "success": self.success,
        }
        if self.content:
            payload["content"] = self.content
        if self.error:
            payload["error"] = self.error
        if self.metadata:
            payload["metadata"] = self.metadata
        return json.dumps(payload, ensure_ascii=False)

    @classmethod
    def ok(
        cls,
        *,
        tool_name: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        tool_call_id: str | None = None,
    ) -> "ToolResult":
        return cls(
            tool_name=tool_name,
            success=True,
            content=content,
            metadata={} if metadata is None else metadata,
            tool_call_id=tool_call_id,
        )

    @classmethod
    def fail(
        cls,
        *,
        tool_name: str,
        error: str,
        metadata: dict[str, Any] | None = None,
        tool_call_id: str | None = None,
    ) -> "ToolResult":
        return cls(
            tool_name=tool_name,
            success=False,
            error=error,
            metadata={} if metadata is None else metadata,
            tool_call_id=tool_call_id,
        )


class Tool(ABC):
    """Base class for local tools exposed to model providers."""

    name: str
    description: str
    input_schema: dict[str, Any]
    requires_confirmation: bool = False

    @abstractmethod
    def execute(self, arguments: Mapping[str, Any], context: ToolContext) -> ToolResult:
        """Execute the tool with provider-neutral arguments."""

    def summarize_arguments(self, arguments: Mapping[str, Any]) -> str:
        """Return a short argument summary for confirmation prompts."""

        text = json.dumps(dict(arguments), ensure_ascii=False)
        if len(text) <= 240:
            return text
        return text[:237] + "..."


def sanitize_text(text: str, context: ToolContext) -> str:
    """Redact known secrets and common key shapes from tool output."""

    sanitized = text
    for secret in context.secrets:
        if secret:
            sanitized = sanitized.replace(secret, "[REDACTED]")
    for pattern in _SECRET_PATTERNS:
        sanitized = pattern.sub("[REDACTED]", sanitized)
    return sanitized


def require_string(arguments: Mapping[str, Any], key: str, tool_name: str) -> str | ToolResult:
    """Read a required string argument or return a structured failure."""

    value = arguments.get(key)
    if not isinstance(value, str) or value == "":
        return ToolResult.fail(tool_name=tool_name, error=f"参数 `{key}` 必须是非空字符串。")
    return value


def clamp_text(text: str, limit: int) -> tuple[str, bool]:
    """Clamp text to a maximum number of characters."""

    if len(text) <= limit:
        return text, False
    return text[:limit] + "\n[输出已截断]", True
