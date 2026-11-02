"""Coordinator Mode helpers for AgentTeam Lead runs."""

from __future__ import annotations

from miniclaudecode.prompting import DynamicInstruction
from miniclaudecode.tools.registry import ToolRegistry

COORDINATOR_TOOLS = ("Task", "TeamCreate", "TeamDelete", "TeamSpawn", "TeamDispatch", "SendMessage")
BLOCKED_DIRECT_TOOLS = ("read_file", "write_file", "edit_file", "glob_files", "grep_content", "run_command")


def coordinator_enabled(*, config_enabled: bool, request_enabled: bool) -> bool:
    """Coordinator Mode requires both configuration and launch request locks."""

    return config_enabled and request_enabled


def coordinator_registry(registry: ToolRegistry) -> ToolRegistry:
    """Return the narrow Coordinator Lead tool registry."""

    return registry.filtered(tuple(name for name in COORDINATOR_TOOLS if name in registry))


def coordinator_instructions() -> tuple[DynamicInstruction, ...]:
    """Inject the fixed four-phase workflow guidance."""

    content = (
        "Coordinator Mode 已启用。Lead 不直接读写代码，不运行 shell。\n"
        "阶段 1 Understand：派队员理解问题和代码，自己只整理问题。\n"
        "阶段 2 Plan：用 Task 建共享任务、依赖关系和队员分工。\n"
        "阶段 3 Execute：用 Task 和 SendMessage 驱动队员执行并等待结果。\n"
        "阶段 4 Synthesize：汇总结果，触发收敛合并，报告冲突并输出最终结论。"
    )
    return (DynamicInstruction(name="agentteam_coordinator_mode", content=content, priority=85, tag="system-reminder"),)
