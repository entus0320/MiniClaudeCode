"""Core data types for SubAgent definitions and runs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from miniclaudecode.providers.base import ProviderUsage
from miniclaudecode.worktrees.core import WorktreeIsolation


class SubAgentError(Exception):
    """Raised when SubAgent setup or execution fails."""


class AgentDefinitionError(SubAgentError):
    """Raised when an AgentDefinition cannot be loaded or validated."""


class AgentDefinitionSourceKind(str, Enum):
    """Supported AgentDefinition source layers."""

    PLUGIN = "plugin"
    BUILTIN = "builtin"
    USER = "user"
    PROJECT = "project"

    @property
    def priority(self) -> int:
        if self is AgentDefinitionSourceKind.PROJECT:
            return 40
        if self is AgentDefinitionSourceKind.USER:
            return 30
        if self is AgentDefinitionSourceKind.BUILTIN:
            return 20
        return 10


class AgentDefinitionPackageKind(str, Enum):
    """How an AgentDefinition is packaged on disk."""

    FILE = "file"
    DIRECTORY = "directory"


class SubAgentLaunchMode(str, Enum):
    """How a SubAgent is spawned."""

    DEFINITION = "definition"
    FORK = "fork"


class SubAgentRunMode(str, Enum):
    """Whether a SubAgent blocks the caller."""

    FOREGROUND = "foreground"
    BACKGROUND = "background"


class SubAgentStatus(str, Enum):
    """Lifecycle state for a SubAgent task."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


@dataclass(frozen=True)
class AgentDefinitionSource:
    """Location and precedence of one AgentDefinition."""

    kind: AgentDefinitionSourceKind
    root_path: Path
    package_path: Path
    entry_path: Path
    package_kind: AgentDefinitionPackageKind

    @property
    def priority(self) -> int:
        return self.kind.priority


@dataclass(frozen=True)
class AgentDefinition:
    """A normalized SubAgent definition loaded from Markdown."""

    name: str
    description: str
    prompt_markdown: str
    source: AgentDefinitionSource
    model: str | None = None
    max_rounds: int | None = None
    permission_mode: str | None = None
    tools: tuple[str, ...] = ()
    disallowed_tools: tuple[str, ...] = ()
    enabled: bool = True
    worktree: WorktreeIsolation = field(default_factory=WorktreeIsolation)


@dataclass(frozen=True)
class AgentDefinitionWarning:
    """A recoverable loading or validation warning."""

    message: str
    path: Path | None = None
    name: str | None = None

    def render(self) -> str:
        parts = [self.message]
        if self.name:
            parts.append(f"定义：{self.name}")
        if self.path is not None:
            parts.append(f"路径：{self.path}")
        return "SubAgent 警告：" + "；".join(parts)


@dataclass(frozen=True)
class AgentDefinitionDiscoveryResult:
    """Result of discovering AgentDefinitions."""

    definitions: tuple[AgentDefinition, ...] = ()
    warnings: tuple[AgentDefinitionWarning, ...] = ()
    overridden: tuple[str, ...] = ()


@dataclass(frozen=True)
class AgentDefinitionReloadResult:
    """Result of reloading AgentDefinitions."""

    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    overridden: tuple[str, ...] = ()
    warnings: tuple[AgentDefinitionWarning, ...] = ()
    error: str | None = None


@dataclass(frozen=True)
class SubAgentUsage:
    """Aggregated token usage for a SubAgent run."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    cache_read_input_tokens: int | None = None

    @classmethod
    def empty(cls) -> "SubAgentUsage":
        return cls()

    def add(self, usage: ProviderUsage | None) -> "SubAgentUsage":
        if usage is None:
            return self
        return SubAgentUsage(
            input_tokens=_sum_optional(self.input_tokens, usage.input_tokens),
            output_tokens=_sum_optional(self.output_tokens, usage.output_tokens),
            cache_creation_input_tokens=_sum_optional(self.cache_creation_input_tokens, usage.cache_creation_input_tokens),
            cache_read_input_tokens=_sum_optional(self.cache_read_input_tokens, usage.cache_read_input_tokens),
        )

    def render(self) -> str:
        return (
            f"input={_render_optional_int(self.input_tokens)}, "
            f"output={_render_optional_int(self.output_tokens)}, "
            f"cache_write={_render_optional_int(self.cache_creation_input_tokens)}, "
            f"cache_read={_render_optional_int(self.cache_read_input_tokens)}"
        )


@dataclass(frozen=True)
class SubAgentSpawnRequest:
    """Request to start a SubAgent."""

    task: str
    subagent_type: str | None = None
    background: bool = False
    timeout_seconds: float | None = None
    tools: tuple[str, ...] | None = None
    disallowed_tools: tuple[str, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class SubAgentRunResult:
    """Final result of a SubAgent run."""

    success: bool
    status: SubAgentStatus
    final_text: str = ""
    error: str | None = None
    usage: SubAgentUsage = field(default_factory=SubAgentUsage.empty)
    trace_id: str | None = None
    task_id: str | None = None

    def to_summary(self) -> str:
        if self.success:
            return self.final_text.strip() or "子 Agent 已完成，但没有返回文本。"
        return self.error or self.final_text or f"子 Agent 结束状态：{self.status.value}"


@dataclass(frozen=True)
class SubAgentNotification:
    """A user-visible background task notification."""

    task_id: str
    status: SubAgentStatus
    title: str
    message: str

    def render(self) -> str:
        return f"SubAgent task-notification：{self.title}\n- task id：{self.task_id}\n- 状态：{self.status.value}\n{self.message}"


def normalize_agent_name(name: str) -> str:
    """Normalize an AgentDefinition name or subagent_type."""

    return name.strip().lower().lstrip("/")


def normalize_tool_names(value: Sequence[str] | None) -> tuple[str, ...]:
    if value is None:
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _sum_optional(left: int | None, right: int | None) -> int | None:
    if right is None:
        return left
    if left is None:
        return right
    return left + right


def _render_optional_int(value: int | None) -> str:
    return "unknown" if value is None else str(value)
