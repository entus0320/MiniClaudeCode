"""RunToCompletion runtime for SubAgents."""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from miniclaudecode.agent import AgentCallbacks, AgentOptions, AgentRunner
from miniclaudecode.context import ContextManager
from miniclaudecode.hooks.engine import HookEngine
from miniclaudecode.policy import PermissionMode, PolicyContext, PolicyEngine, SessionPolicy
from miniclaudecode.prompting import DynamicInstruction, PromptAssembler, RuntimeContext
from miniclaudecode.providers.base import BaseProvider
from miniclaudecode.session import Message, Session
from miniclaudecode.subagents.core import (
    AgentDefinition,
    SubAgentLaunchMode,
    SubAgentRunResult,
    SubAgentSpawnRequest,
    SubAgentStatus,
)
from miniclaudecode.subagents.fork import ForkContextBuilder
from miniclaudecode.subagents.tools import SubAgentToolFilter, ToolFilterConfig
from miniclaudecode.subagents.trace import TraceRegistry
from miniclaudecode.tools.base import ToolContext
from miniclaudecode.tools.registry import ToolRegistry
from miniclaudecode.worktrees.adapter import SubAgentWorktreeAdapter

RuntimeContextFactory = Callable[[], RuntimeContext]


@dataclass(frozen=True)
class SubAgentRuntimeDependencies:
    """Shared dependencies required to spawn a SubAgent."""

    provider: BaseProvider
    parent_session: Session
    tool_registry: ToolRegistry
    tool_context: ToolContext
    prompt_assembler: PromptAssembler | None = None
    runtime_context_factory: RuntimeContextFactory | None = None
    policy_engine: PolicyEngine | None = None
    policy_context: PolicyContext | None = None
    context_manager: ContextManager | None = None
    hook_engine: HookEngine | None = None
    trace_registry: TraceRegistry | None = None
    worktree_adapter: SubAgentWorktreeAdapter | None = None
    background_allowed_tools: tuple[str, ...] | None = None
    default_max_rounds: int = 8


class SubAgentRuntime:
    """Create isolated child AgentRunner instances and run them to completion."""

    def __init__(self, dependencies: SubAgentRuntimeDependencies) -> None:
        self.dependencies = dependencies
        self.tool_filter = SubAgentToolFilter()
        self.fork_builder = ForkContextBuilder()

    def run_to_completion(
        self,
        request: SubAgentSpawnRequest,
        *,
        definition: AgentDefinition | None = None,
        cancel_event: threading.Event | None = None,
        trace_id: str | None = None,
        task_id: str | None = None,
        parent_history: Sequence[Message] | None = None,
    ) -> SubAgentRunResult:
        """Run one SubAgent request to completion."""

        launch_mode = SubAgentLaunchMode.DEFINITION if definition is not None else SubAgentLaunchMode.FORK
        worktree_session = None
        tool_context = self.dependencies.tool_context
        runtime_context_factory = self.dependencies.runtime_context_factory
        if definition is not None and self.dependencies.worktree_adapter is not None:
            worktree_session = self.dependencies.worktree_adapter.acquire(definition, task_id=task_id)
            if worktree_session is not None:
                tool_context = ToolContext(
                    worktree_session.record.path,
                    command_timeout_seconds=self.dependencies.tool_context.command_timeout_seconds,
                    max_read_bytes=self.dependencies.tool_context.max_read_bytes,
                    max_output_chars=self.dependencies.tool_context.max_output_chars,
                    max_results=self.dependencies.tool_context.max_results,
                    secrets=self.dependencies.tool_context.secrets,
                )
                if runtime_context_factory is not None:
                    parent_factory = runtime_context_factory

                    def runtime_context_factory() -> RuntimeContext:
                        context = parent_factory()
                        return RuntimeContext(
                            workdir=worktree_session.record.path,
                            os_name=context.os_name,
                            current_date=context.current_date,
                            provider_name=context.provider_name,
                            model=context.model,
                            git_status=context.git_status,
                        )

        child_session = Session(tool_result_slimmer=self.dependencies.parent_session.tool_result_slimmer)
        task_text = request.task
        if worktree_session is not None:
            task_text = f"{worktree_session.translation_instruction}\n\n任务：\n{request.task}"
        if launch_mode is SubAgentLaunchMode.FORK:
            fork_context = self.fork_builder.build(parent_history or self.dependencies.parent_session.get_history(), task_text)
            child_session.replace_history(fork_context.history)
        else:
            child_session.add_user_message(task_text)

        background = request.background or launch_mode is SubAgentLaunchMode.FORK
        isolated_worktree = worktree_session is not None
        background_allowed_tools = self.dependencies.background_allowed_tools
        if isolated_worktree and definition is not None and definition.tools:
            background_allowed_tools = tuple(dict.fromkeys((*tuple(background_allowed_tools or ()), *definition.tools)))
        filter_result = self.tool_filter.filter(
            self.dependencies.tool_registry,
            definition=definition,
            config=ToolFilterConfig(
                background_allowed=background_allowed_tools,
                custom_allowed=request.tools,
                custom_disallowed=request.disallowed_tools,
            ),
            background=background,
        )
        dynamic_instructions = _subagent_instructions(request, definition, launch_mode)
        child_policy_context = _child_policy_context(self.dependencies.policy_context, tool_context, definition, isolated_worktree=isolated_worktree)
        child_hook_engine = None
        if self.dependencies.hook_engine is not None:
            child_hook_engine = HookEngine(self.dependencies.hook_engine.registry, tool_context=tool_context)
        max_rounds = definition.max_rounds if definition is not None and definition.max_rounds is not None else self.dependencies.default_max_rounds
        runner = AgentRunner(
            self.dependencies.provider,
            child_session,
            filter_result.registry,
            tool_context,
            AgentCallbacks(confirm_tool=_allow_worktree_subagent_tool if isolated_worktree else None),
            AgentOptions(max_rounds=max_rounds, is_cancelled=(cancel_event.is_set if cancel_event is not None else None)),
            prompt_assembler=self.dependencies.prompt_assembler,
            runtime_context_factory=runtime_context_factory,
            policy_engine=self.dependencies.policy_engine,
            policy_context=child_policy_context,
            context_manager=None,
            generation_watch_config=None,
            hook_engine=child_hook_engine,
            dynamic_instructions=dynamic_instructions,
        )
        final_text = runner.run(child_session.get_history())
        cleanup_result = None
        if worktree_session is not None and self.dependencies.worktree_adapter is not None:
            cleanup_result = self.dependencies.worktree_adapter.finish(worktree_session)
            if cleanup_result is not None and cleanup_result.message:
                final_text = f"{final_text}\n\n{cleanup_result.message}" if final_text else cleanup_result.message
        if cancel_event is not None and cancel_event.is_set():
            return SubAgentRunResult(
                success=False,
                status=SubAgentStatus.CANCELLED,
                final_text=final_text,
                error="子 Agent 已取消。",
                usage=runner.state.usage.to_subagent_usage(),
                trace_id=trace_id,
                task_id=task_id,
            )
        if runner.state.stop_reason == "error":
            return SubAgentRunResult(
                success=False,
                status=SubAgentStatus.FAILED,
                final_text=final_text,
                error="子 Agent 执行失败。",
                usage=runner.state.usage.to_subagent_usage(),
                trace_id=trace_id,
                task_id=task_id,
            )
        return SubAgentRunResult(
            success=True,
            status=SubAgentStatus.COMPLETED,
            final_text=final_text,
            usage=runner.state.usage.to_subagent_usage(),
            trace_id=trace_id,
            task_id=task_id,
        )


def _subagent_instructions(
    request: SubAgentSpawnRequest,
    definition: AgentDefinition | None,
    launch_mode: SubAgentLaunchMode,
) -> tuple[DynamicInstruction, ...]:
    base = (
        "你正在作为父 Agent 启动的子 Agent 工作。必须围绕单个任务跑到底，不要等待用户输入。"
        "不要启动其他子 Agent。最终只返回对父 Agent 有用的结果。"
    )
    instructions = [DynamicInstruction(name="subagent_runtime", content=base, priority=80, tag="system-reminder")]
    if definition is not None:
        instructions.append(
            DynamicInstruction(
                name=f"subagent_definition_{definition.name}",
                content=f"当前 subagent_type：{definition.name}\n说明：{definition.description}\n\n{definition.prompt_markdown}",
                priority=70,
                tag="system-reminder",
            )
        )
    if launch_mode is SubAgentLaunchMode.FORK:
        instructions.append(
            DynamicInstruction(
                name="subagent_fork_mode",
                content="当前为 Fork 子 Agent。你继承了父 Agent 的历史快照，但后续状态与父 Agent 隔离。",
                priority=75,
                tag="system-reminder",
            )
        )
    return tuple(instructions)


def _child_policy_context(
    parent: PolicyContext | None,
    tool_context: ToolContext,
    definition: AgentDefinition | None,
    *,
    isolated_worktree: bool = False,
) -> PolicyContext:
    if parent is None:
        return PolicyContext(workdir=tool_context.workdir)
    mode = parent.permission_mode
    if definition is not None and definition.permission_mode is not None:
        requested = PermissionMode(definition.permission_mode)
        mode = _stricter_mode(parent.permission_mode, requested)
    return PolicyContext(
        workdir=tool_context.workdir,
        permission_mode=mode,
        project_rules=parent.project_rules,
        session_policy=SessionPolicy(),
        plan_only=parent.plan_only,
    )


def _allow_worktree_subagent_tool(call, tool, decision):  # type: ignore[no-untyped-def]
    _ = call, decision
    if tool.name in {"write_file", "edit_file"}:
        return "allow_session"
    return "deny"


def _stricter_mode(left: PermissionMode, right: PermissionMode) -> PermissionMode:
    rank = {PermissionMode.STRICT: 3, PermissionMode.DEFAULT: 2, PermissionMode.PERMISSIVE: 1}
    return left if rank[left] >= rank[right] else right
