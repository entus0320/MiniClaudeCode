"""Tool filtering for SubAgent runs."""

from __future__ import annotations

from dataclasses import dataclass, field

from miniclaudecode.subagents.core import AgentDefinition
from miniclaudecode.tools.registry import ToolRegistry

AGENT_TOOL_NAME = "agent"
TEAM_TOOL_NAMES = ("Task", "SendMessage", "TeamCreate", "TeamDelete", "TeamSpawn", "TeamDispatch")


@dataclass(frozen=True)
class ToolFilterConfig:
    """Runtime tool restrictions for one SubAgent run."""

    global_disallowed: tuple[str, ...] = (AGENT_TOOL_NAME, *TEAM_TOOL_NAMES)
    background_allowed: tuple[str, ...] | None = None
    custom_allowed: tuple[str, ...] | None = None
    custom_disallowed: tuple[str, ...] = ()


@dataclass(frozen=True)
class ToolFilterResult:
    """Filtered ToolRegistry plus an explanation of removed tools."""

    registry: ToolRegistry
    allowed_names: tuple[str, ...]
    removed: dict[str, str] = field(default_factory=dict)


class SubAgentToolFilter:
    """Apply SubAgent tool restrictions in a monotonic, narrowing order."""

    def __init__(self, *, agent_tool_name: str = AGENT_TOOL_NAME) -> None:
        self.agent_tool_name = agent_tool_name

    def filter(
        self,
        parent_registry: ToolRegistry,
        *,
        definition: AgentDefinition | None = None,
        config: ToolFilterConfig | None = None,
        background: bool = False,
    ) -> ToolFilterResult:
        """Return a child registry that only contains allowed tools."""

        filter_config = config or ToolFilterConfig(global_disallowed=(self.agent_tool_name, *TEAM_TOOL_NAMES))
        available = set(parent_registry.list_tool_names())
        allowed = set(available)
        removed: dict[str, str] = {}

        def remove(names: set[str], reason: str) -> None:
            for name in sorted(names & allowed):
                allowed.remove(name)
                removed.setdefault(name, reason)

        def restrict_to(names: set[str], reason: str) -> None:
            remove(allowed - names, reason)

        remove(set(filter_config.global_disallowed) | {self.agent_tool_name}, "global_disallowed")
        if filter_config.custom_allowed is not None:
            restrict_to(set(filter_config.custom_allowed), "custom_allowed")
        remove(set(filter_config.custom_disallowed), "custom_disallowed")
        if background and filter_config.background_allowed is not None:
            restrict_to(set(filter_config.background_allowed), "background_allowed")
        if definition is not None and definition.tools:
            restrict_to(set(definition.tools), "definition_tools")
        if definition is not None and definition.disallowed_tools:
            remove(set(definition.disallowed_tools), "definition_disallowedTools")

        child = parent_registry.filtered([name for name in parent_registry.list_tool_names() if name in allowed])
        return ToolFilterResult(
            registry=child,
            allowed_names=tuple(child.list_tool_names()),
            removed=removed,
        )
