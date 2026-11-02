"""Runtime registry for AgentDefinitions."""

from __future__ import annotations

from miniclaudecode.subagents.core import AgentDefinition, AgentDefinitionError, AgentDefinitionReloadResult, AgentDefinitionWarning, normalize_agent_name
from miniclaudecode.subagents.loader import AgentDefinitionLoader
from miniclaudecode.subagents.validator import validate_agent_against_registry
from miniclaudecode.tools.registry import ToolRegistry


class AgentDefinitionRegistry:
    """Hold discovered AgentDefinitions and support safe reloads."""

    def __init__(self, loader: AgentDefinitionLoader, *, tool_registry: ToolRegistry | None = None) -> None:
        self.loader = loader
        self.tool_registry = tool_registry
        self._definitions: dict[str, AgentDefinition] = {}
        self._warnings: tuple[AgentDefinitionWarning, ...] = ()
        self._overridden: tuple[str, ...] = ()

    def load_initial(self) -> None:
        """Load AgentDefinitions at startup."""

        definitions, warnings, overridden = self._build_state()
        self._replace_state(definitions, warnings, overridden)

    def reload(self) -> AgentDefinitionReloadResult:
        """Reload AgentDefinitions. Keep the old state if reload fails."""

        old_names = set(self._definitions)
        try:
            definitions, warnings, overridden = self._build_state()
        except Exception as exc:
            return AgentDefinitionReloadResult(warnings=self._warnings, error=str(exc))
        new_names = set(definitions)
        self._replace_state(definitions, warnings, overridden)
        return AgentDefinitionReloadResult(
            added=tuple(sorted(new_names - old_names)),
            removed=tuple(sorted(old_names - new_names)),
            overridden=overridden,
            warnings=warnings,
        )

    def list_definitions(self) -> list[AgentDefinition]:
        return [self._definitions[name] for name in sorted(self._definitions)]

    def get(self, name: str) -> AgentDefinition:
        key = normalize_agent_name(name)
        try:
            return self._definitions[key]
        except KeyError as exc:
            raise AgentDefinitionError(f"未知 subagent_type：{name}") from exc

    def has(self, name: str) -> bool:
        return normalize_agent_name(name) in self._definitions

    @property
    def warnings(self) -> tuple[AgentDefinitionWarning, ...]:
        return self._warnings

    @property
    def overridden(self) -> tuple[str, ...]:
        return self._overridden

    def summary_text(self) -> str:
        if not self._definitions:
            return "当前没有可用 AgentDefinition。"
        lines = ["可用 SubAgent 类型："]
        for definition in self.list_definitions():
            lines.append(f"- {definition.name}: {definition.description}")
        return "\n".join(lines)

    def detail_text(self, name: str) -> str:
        definition = self.get(name)
        tools = ", ".join(definition.tools) or "继承过滤后工具集"
        disallowed = ", ".join(definition.disallowed_tools) or "无"
        return (
            f"{definition.name}\n"
            f"说明：{definition.description}\n"
            f"来源：{definition.source.kind.value}\n"
            f"入口：{definition.source.entry_path}\n"
            f"模型：{definition.model or '父模型'}\n"
            f"最大轮次：{definition.max_rounds or '默认'}\n"
            f"权限模式：{definition.permission_mode or '继承父级'}\n"
            f"工具白名单：{tools}\n"
            f"工具黑名单：{disallowed}"
        )

    def _build_state(self) -> tuple[dict[str, AgentDefinition], tuple[AgentDefinitionWarning, ...], tuple[str, ...]]:
        result = self.loader.discover()
        definitions: dict[str, AgentDefinition] = {}
        for definition in result.definitions:
            validate_agent_against_registry(definition, self.tool_registry)
            definitions[definition.name] = definition
        return definitions, result.warnings, result.overridden

    def _replace_state(
        self,
        definitions: dict[str, AgentDefinition],
        warnings: tuple[AgentDefinitionWarning, ...],
        overridden: tuple[str, ...],
    ) -> None:
        self._definitions = dict(definitions)
        self._warnings = tuple(warnings)
        self._overridden = tuple(overridden)
