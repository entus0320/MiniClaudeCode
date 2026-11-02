"""Runtime Skill activation and prompt/tool visibility integration."""

from __future__ import annotations

from dataclasses import dataclass

from miniclaudecode.prompting import DynamicInstruction
from miniclaudecode.skills.core import SkillDefinition, SkillMode
from miniclaudecode.skills.registry import SkillRegistry
from miniclaudecode.tools.base import Tool


@dataclass(frozen=True)
class SkillActivationResult:
    """Observable activation result."""

    skill: SkillDefinition
    message: str
    isolated_mode_degraded: bool = False


class SkillRuntime:
    """Maintain active Skills for one TUI session."""

    def __init__(self, registry: SkillRegistry) -> None:
        self.registry = registry
        self._active_order: list[str] = []
        self._active_snapshots: dict[str, SkillDefinition] = {}

    def activate(self, name: str) -> SkillActivationResult:
        """Activate a Skill, refreshing its source for hot updates."""

        skill = self.registry.refresh_one(name)
        if skill.name not in self._active_snapshots:
            self._active_order.append(skill.name)
        self._active_snapshots[skill.name] = skill
        degraded = skill.metadata.mode is SkillMode.ISOLATED
        if degraded:
            message = f"已激活 Skill：/{skill.name}。隔离模式已预留，当前未实现；本阶段按 shared 模式注入 SOP。"
        else:
            message = f"已激活 Skill：/{skill.name}。"
        return SkillActivationResult(skill=skill, message=message, isolated_mode_degraded=degraded)

    def clear_active(self) -> None:
        """Clear active Skills for a new-conversation boundary."""

        self._active_order.clear()
        self._active_snapshots.clear()

    def active_skills(self) -> list[SkillDefinition]:
        return [self._active_snapshots[name] for name in self._active_order if name in self._active_snapshots]

    def summary_instruction(self) -> DynamicInstruction | None:
        text = self.registry.summary_text().strip()
        if not text:
            return None
        return DynamicInstruction(name="available_skills", content=text, priority=200, tag="skills")

    def active_instructions(self) -> list[DynamicInstruction]:
        instructions: list[DynamicInstruction] = []
        for index, skill in enumerate(self.active_skills()):
            instructions.append(
                DynamicInstruction(
                    name=f"active_skill_{skill.name}",
                    content=(
                        f"当前已激活 Skill：/{skill.name}\n"
                        f"说明：{skill.description}\n"
                        "以下是该 Skill 的 SOP 指令，必须在当前任务中遵循：\n\n"
                        f"{skill.sop_markdown}"
                    ),
                    priority=300 + index,
                    tag="active-skill",
                )
            )
        return instructions

    def dynamic_instructions(self) -> list[DynamicInstruction]:
        instructions: list[DynamicInstruction] = []
        summary = self.summary_instruction()
        if summary is not None:
            instructions.append(summary)
        instructions.extend(self.active_instructions())
        return instructions

    def visible_tools(self, tools: list[Tool]) -> list[Tool]:
        allowed_names = self._active_tool_names()
        if allowed_names is None:
            return tools
        return [tool for tool in tools if tool.name in allowed_names]

    def _active_tool_names(self) -> set[str] | None:
        active = self.active_skills()
        whitelists = [set(skill.metadata.tools) for skill in active if skill.metadata.tools]
        if not whitelists:
            return None
        allowed: set[str] = set()
        for items in whitelists:
            allowed.update(items)
        for skill in active:
            for spec in skill.directory_tools:
                allowed.add(f"{skill.name}__{spec.name}")
        return allowed
