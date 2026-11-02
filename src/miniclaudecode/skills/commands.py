"""Slash command adapters for Skills."""

from __future__ import annotations

from miniclaudecode.commands.core import Command, CommandInvocation, CommandRegistry, CommandResult, CommandType
from miniclaudecode.skills.core import SkillDiscoveryError
from miniclaudecode.skills.registry import SkillRegistry
from miniclaudecode.skills.runtime import SkillRuntime


def register_skill_commands(registry: CommandRegistry, skill_registry: SkillRegistry, skill_runtime: SkillRuntime) -> None:
    """Register Skill management commands and one command per discovered Skill."""

    registry.register(
        Command(
            name="skills",
            description="列出可用 Skill。",
            usage="/skills",
            type=CommandType.LOCAL,
            handler=lambda ctx, invocation: _handle_skills(skill_registry, skill_runtime),
        )
    )
    registry.register(
        Command(
            name="skill",
            description="显示单个 Skill 详情。",
            usage="/skill <name>",
            type=CommandType.LOCAL,
            argument_hint="<name>",
            handler=lambda ctx, invocation: _handle_skill_detail(skill_registry, invocation),
        )
    )
    registry.register(
        Command(
            name="skills-reload",
            description="重新扫描 Skill。",
            usage="/skills-reload",
            type=CommandType.UI_STATE,
            handler=lambda ctx, invocation: _handle_skills_reload(skill_registry, skill_runtime),
        )
    )
    for skill in skill_registry.list_skills():
        registry.register(_make_skill_command(skill.name, skill.description, skill_runtime))


def _make_skill_command(skill_name: str, description: str, skill_runtime: SkillRuntime) -> Command:
    return Command(
        name=skill_name,
        description=f"激活 Skill：{description}",
        usage=f"/{skill_name}",
        type=CommandType.LOCAL,
        handler=lambda ctx, invocation: _handle_skill_activate(skill_runtime, skill_name),
    )


def _handle_skills(skill_registry: SkillRegistry, skill_runtime: SkillRuntime) -> CommandResult:
    active = {skill.name for skill in skill_runtime.active_skills()}
    skills = skill_registry.list_skills()
    if not skills:
        return CommandResult(message="当前没有可用 Skill。")
    lines = ["可用 Skill："]
    for skill in skills:
        marker = "已激活" if skill.name in active else "未激活"
        lines.append(f"- /{skill.name} [{marker}]：{skill.description}")
    return CommandResult(message="\n".join(lines))


def _handle_skill_detail(skill_registry: SkillRegistry, invocation: CommandInvocation) -> CommandResult:
    name = invocation.args.strip().lstrip("/")
    if not name:
        return CommandResult(message="用法：/skill <name>")
    try:
        return CommandResult(message=skill_registry.detail_text(name))
    except SkillDiscoveryError as exc:
        return CommandResult(message=str(exc))


def _handle_skills_reload(skill_registry: SkillRegistry, skill_runtime: SkillRuntime) -> CommandResult:
    old_skill_commands = {skill.name for skill in skill_registry.list_skills()}
    result = skill_registry.reload()
    if not result.success:
        return CommandResult(message=f"Skill 重扫失败，已保留旧状态：{result.error}")
    for name in old_skill_commands:
        skill_registry.command_registry.unregister(name)
    for skill in skill_registry.list_skills():
        skill_registry.command_registry.register(_make_skill_command(skill.name, skill.description, skill_runtime))
    skill_runtime.clear_active()
    lines = ["Skill 重扫完成："]
    lines.append(f"- 新增：{len(result.added)}")
    lines.append(f"- 移除：{len(result.removed)}")
    lines.append(f"- 覆盖：{len(result.overridden)}")
    lines.append(f"- 警告：{len(result.warnings)}")
    if result.added:
        lines.append(f"新增 Skill：{', '.join(result.added)}")
    if result.removed:
        lines.append(f"移除 Skill：{', '.join(result.removed)}")
    return CommandResult(message="\n".join(lines))


def _handle_skill_activate(skill_runtime: SkillRuntime, skill_name: str) -> CommandResult:
    try:
        result = skill_runtime.activate(skill_name)
    except Exception as exc:
        return CommandResult(message=f"Skill 激活失败：/{skill_name}\n- 详情：{exc}")
    return CommandResult(message=result.message)
