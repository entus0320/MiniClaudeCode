"""Runtime registry for discovered Skills."""

from __future__ import annotations

from pathlib import Path

from miniclaudecode.commands.core import CommandRegistry
from miniclaudecode.skills.core import SkillDefinition, SkillDiscoveryError, SkillReloadResult, SkillWarning
from miniclaudecode.skills.directory_tools import build_directory_skill_tools
from miniclaudecode.skills.loader import SkillLoader
from miniclaudecode.skills.validator import validate_skill_against_registries
from miniclaudecode.tools.registry import ToolRegistry


class SkillRegistry:
    """Hold discovered Skills and support safe reloads."""

    def __init__(
        self,
        loader: SkillLoader,
        *,
        tool_registry: ToolRegistry,
        command_registry: CommandRegistry | None = None,
    ) -> None:
        self.loader = loader
        self.tool_registry = tool_registry
        self.command_registry = command_registry
        self._skills: dict[str, SkillDefinition] = {}
        self._warnings: tuple[SkillWarning, ...] = ()
        self._overridden: tuple[str, ...] = ()
        self._directory_tool_names: tuple[str, ...] = ()

    def load_initial(self) -> None:
        """Load Skills at startup, failing fast for invalid runtime state."""

        skills, warnings, overridden, tool_names = self._build_state()
        self._replace_state(skills, warnings, overridden, tool_names)

    def reload(self) -> SkillReloadResult:
        """Reload Skills. Keep the old state if reload fails."""

        old_names = set(self._skills)
        try:
            skills, warnings, overridden, tool_names = self._build_state()
        except Exception as exc:
            return SkillReloadResult(warnings=self._warnings, error=str(exc))
        new_names = set(skills)
        self._replace_state(skills, warnings, overridden, tool_names)
        return SkillReloadResult(
            added=tuple(sorted(new_names - old_names)),
            removed=tuple(sorted(old_names - new_names)),
            overridden=overridden,
            warnings=warnings,
        )

    def refresh_one(self, name: str) -> SkillDefinition:
        """Reload one Skill from its source before activation."""

        current = self.get(name)
        refreshed = self.loader.load_entry(current.source.package_path, current.source.kind, current.source.root_path)
        validate_skill_against_registries(refreshed.metadata, tool_registry=self.tool_registry)
        self._skills[current.name] = refreshed
        return refreshed

    def list_skills(self) -> list[SkillDefinition]:
        return [self._skills[name] for name in sorted(self._skills)]

    def get(self, name: str) -> SkillDefinition:
        key = name.strip().lower().lstrip("/")
        try:
            return self._skills[key]
        except KeyError as exc:
            raise SkillDiscoveryError(f"未知 Skill：{name}") from exc

    def has(self, name: str) -> bool:
        return name.strip().lower().lstrip("/") in self._skills

    @property
    def warnings(self) -> tuple[SkillWarning, ...]:
        return self._warnings

    def summary_text(self) -> str:
        if not self._skills:
            return "当前没有可用 Skill。"
        lines = ["可用 Skill（按需使用，激活后会加载完整 SOP）："]
        for skill in self.list_skills():
            lines.append(f"- /{skill.name}: {skill.description}")
        return "\n".join(lines)

    def detail_text(self, name: str) -> str:
        skill = self.get(name)
        tools = ", ".join(skill.metadata.tools) or "无"
        directory_tools = ", ".join(f"{skill.name}__{spec.name}" for spec in skill.directory_tools) or "无"
        recent = f" ({skill.metadata.recent_messages})" if skill.metadata.recent_messages is not None else ""
        return (
            f"/{skill.name}\n"
            f"说明：{skill.description}\n"
            f"来源：{skill.source.kind.value}\n"
            f"入口：{skill.source.entry_path}\n"
            f"模式：{skill.metadata.mode.value}\n"
            f"模型：{skill.metadata.model or '默认'}\n"
            f"上下文策略：{skill.metadata.context_strategy.value}{recent}\n"
            f"工具白名单：{tools}\n"
            f"目录工具：{directory_tools}"
        )

    def _build_state(self) -> tuple[dict[str, SkillDefinition], tuple[SkillWarning, ...], tuple[str, ...], tuple[str, ...]]:
        result = self.loader.discover()
        skills: dict[str, SkillDefinition] = {}
        directory_tools = []
        tool_names: list[str] = []
        for skill in result.skills:
            self._validate_command_conflict(skill)
            validate_skill_against_registries(
                skill.metadata,
                tool_registry=self.tool_registry,
            )
            if skill.directory_tools:
                built_tools = build_directory_skill_tools(skill.name, skill.source.package_path, skill.directory_tools)
                directory_tools.extend(built_tools)
                tool_names.extend(tool.name for tool in built_tools)
            skills[skill.name] = skill

        for tool in directory_tools:
            if tool.name in self.tool_registry and tool.name not in self._directory_tool_names:
                raise SkillDiscoveryError(f"目录型 Skill 工具名冲突：{tool.name}")
        return skills, result.warnings, result.overridden, tuple(tool_names)

    def _validate_command_conflict(self, skill: SkillDefinition) -> None:
        if self.command_registry is None:
            return
        command = self.command_registry.get(skill.name)
        if command is not None and command.name != skill.name:
            raise SkillDiscoveryError(f"Skill 命令名与已有命令冲突：/{skill.name}")
        if command is not None and command.name == skill.name and skill.name not in self._skills:
            raise SkillDiscoveryError(f"Skill 命令名与已有命令冲突：/{skill.name}")

    def _replace_state(
        self,
        skills: dict[str, SkillDefinition],
        warnings: tuple[SkillWarning, ...],
        overridden: tuple[str, ...],
        tool_names: tuple[str, ...],
    ) -> None:
        for name in self._directory_tool_names:
            if name in self.tool_registry:
                self.tool_registry.unregister(name)
        for skill in skills.values():
            for tool in build_directory_skill_tools(skill.name, skill.source.package_path, skill.directory_tools):
                self.tool_registry.register(tool)
        self._skills = dict(skills)
        self._warnings = tuple(warnings)
        self._overridden = tuple(overridden)
        self._directory_tool_names = tuple(tool_names)
