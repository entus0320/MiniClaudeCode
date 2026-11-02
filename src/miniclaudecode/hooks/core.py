"""Core Hook rule models for MiniClaudeCode."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from miniclaudecode.prompting import DynamicInstruction
from miniclaudecode.tools.base import ToolCall, ToolResult


class HookError(Exception):
    """Base class for Hook errors."""


class HookValidationError(HookError):
    """Raised when one Hook rule is invalid."""


class HookSourceKind(str, Enum):
    """Where a Hook rule was loaded from."""

    PROJECT = "project"
    USER = "user"


class HookEventName(str, Enum):
    """Supported Hook lifecycle events."""

    SYSTEM_START = "system_start"
    SYSTEM_EXIT = "system_exit"
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    AGENT_TURN_START = "agent_turn_start"
    AGENT_TURN_END = "agent_turn_end"
    MESSAGE_BEFORE_SEND = "message_before_send"
    MESSAGE_AFTER_RECEIVE = "message_after_receive"
    TOOL_BEFORE_EXECUTE = "tool_before_execute"
    TOOL_AFTER_EXECUTE = "tool_after_execute"
    ERROR = "error"
    COMPACT = "compact"


class HookActionType(str, Enum):
    """Supported Hook action executors."""

    SHELL = "shell"
    PROMPT_INJECTION = "prompt_injection"
    HTTP = "http"
    SUB_AGENT = "sub_agent"


class HookConditionOperator(str, Enum):
    """Supported condition matching operators."""

    EXACT = "exact"
    NOT = "not"
    REGEX = "regex"
    GLOB = "glob"


@dataclass(frozen=True)
class HookSource:
    """Source location and priority for one Hook rule."""

    kind: HookSourceKind
    root_path: Path
    path: Path

    @property
    def priority(self) -> int:
        if self.kind is HookSourceKind.PROJECT:
            return 30
        return 20


@dataclass(frozen=True)
class HookCondition:
    """One field condition in a Hook rule."""

    field: str
    operator: HookConditionOperator
    value: str


@dataclass(frozen=True)
class HookConditionGroup:
    """A flat all/any condition group."""

    mode: str
    conditions: tuple[HookCondition, ...] = ()


@dataclass(frozen=True)
class HookAction:
    """One action attached to a Hook rule."""

    type: HookActionType
    config: dict[str, Any] = field(default_factory=dict)
    timeout_seconds: float | None = None
    trusted: bool = False
    async_execute: bool = False


@dataclass(frozen=True)
class HookRule:
    """A complete validated Hook rule."""

    name: str
    event: HookEventName
    actions: tuple[HookAction, ...]
    source: HookSource
    declaration_index: int
    conditions: HookConditionGroup | None = None
    intercept_reason: str | None = None
    once: bool = False


@dataclass(frozen=True)
class HookDiagnostic:
    """User-visible Hook loading or execution diagnostic."""

    message: str
    kind: str = "info"
    rule_name: str | None = None
    event: HookEventName | str | None = None
    source_path: Path | None = None
    action_type: HookActionType | str | None = None

    def render(self) -> str:
        parts = [f"Hook {self.kind}：{self.message}"]
        if self.rule_name:
            parts.append(f"Rule: {self.rule_name}")
        if self.event:
            value = self.event.value if isinstance(self.event, HookEventName) else str(self.event)
            parts.append(f"Event: {value}")
        if self.action_type:
            value = self.action_type.value if isinstance(self.action_type, HookActionType) else str(self.action_type)
            parts.append(f"Action: {value}")
        if self.source_path is not None:
            parts.append(f"Path: {self.source_path}")
        return " | ".join(parts)


@dataclass(frozen=True)
class HookInterceptResult:
    """A tool-before Hook decision that blocks original tool execution."""

    blocked: bool = False
    reason: str = ""
    rule_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class HookTriggerResult:
    """Result from triggering Hook rules for one event."""

    diagnostics: tuple[HookDiagnostic, ...] = ()
    dynamic_instructions: tuple[DynamicInstruction, ...] = ()
    intercept: HookInterceptResult | None = None


@dataclass(frozen=True)
class HookConfirmationRequest:
    """Confirmation request for a side-effecting Hook action."""

    rule_name: str
    event: HookEventName
    action_type: HookActionType
    summary: str
    detail: str


@dataclass(frozen=True)
class HookContext:
    """Runtime facts exposed to Hook matching and action templates."""

    event: HookEventName
    workdir: Path | None = None
    message: dict[str, Any] | None = None
    tool: dict[str, Any] | None = None
    tool_call: ToolCall | None = None
    tool_result: ToolResult | None = None
    error: dict[str, Any] | None = None
    round_index: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_mapping(self) -> dict[str, Any]:
        tool = dict(self.tool or {})
        if self.tool_call is not None:
            tool.setdefault("name", self.tool_call.name)
            tool.setdefault("arguments", self.tool_call.arguments)
            tool.setdefault("id", self.tool_call.id)
        result: dict[str, Any] | None = None
        if self.tool_result is not None:
            result = {
                "tool_name": self.tool_result.tool_name,
                "success": self.tool_result.success,
                "content": self.tool_result.content,
                "error": self.tool_result.error or "",
                "metadata": self.tool_result.metadata,
            }
        mapping: dict[str, Any] = {
            "event": self.event.value,
            "workdir": "" if self.workdir is None else str(self.workdir),
            "message": self.message or {},
            "tool": tool,
            "tool_result": result or {},
            "error": self.error or {},
            "round_index": "" if self.round_index is None else self.round_index,
        }
        mapping.update(self.extra)
        return mapping


@dataclass(frozen=True)
class HookDiscoveryResult:
    """Raw Hook discovery result before registry replacement."""

    rules: tuple[HookRule, ...] = ()
    diagnostics: tuple[HookDiagnostic, ...] = ()
    overridden: tuple[str, ...] = ()


@dataclass(frozen=True)
class HookReloadResult:
    """Observable result from reloading Hook rules."""

    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    overridden: tuple[str, ...] = ()
    diagnostics: tuple[HookDiagnostic, ...] = ()
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.error is None
