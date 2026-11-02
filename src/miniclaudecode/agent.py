"""Finite ReAct agent loop orchestration for MiniClaudeCode."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Generator, Iterable, Sequence
from dataclasses import dataclass, field
from enum import Enum

from miniclaudecode.context import ContextManagementResult, ContextManager
from miniclaudecode.hooks.core import HookContext, HookDiagnostic, HookEventName
from miniclaudecode.hooks.engine import HookEngine
from miniclaudecode.observability import GenerationWatchConfig, GenerationWatchEvent, GenerationWatchdog
from miniclaudecode.policy import PolicyAction, PolicyContext, PolicyDecision, PolicyEngine
from miniclaudecode.prompting import DynamicInstruction, PromptAssembler, RuntimeContext, plan_only_instruction
from miniclaudecode.providers.base import BaseProvider, ProviderError, ProviderTextDelta, ProviderTurnComplete, ProviderUsage
from miniclaudecode.session import Message, Session, ToolResultOffloadInfo
from miniclaudecode.skills.runtime import SkillRuntime
from miniclaudecode.tools.base import Tool, ToolCall, ToolContext, ToolResult
from miniclaudecode.tools.registry import ToolRegistry, ToolRegistryError

class ConfirmationResponse(str, Enum):
    """User confirmation result for an ask policy decision."""

    ALLOW_ONCE = "allow_once"
    ALLOW_SESSION = "allow_session"
    DENY = "deny"


ConfirmTool = Callable[..., bool | ConfirmationResponse]
TextCallback = Callable[[str], None]
ToolCallback = Callable[[ToolCall], None]
ToolResultCallback = Callable[[ToolResult], None]
StatusCallback = Callable[[str], None]
CancelCheck = Callable[[], bool]
RuntimeContextFactory = Callable[[], RuntimeContext]

PLAN_ONLY_READ_TOOLS = frozenset({"read_file", "glob_files", "grep_content"})
PARALLEL_SAFE_LOCAL_TOOLS = PLAN_ONLY_READ_TOOLS
DEFAULT_MAX_ROUNDS = 20 #单轮最大工具调用数
INSUFFICIENT_INFORMATION_MESSAGE = "当前信息不足以回答该问题。"


@dataclass(frozen=True)
class AgentOptions:
    """Runtime options for one finite agent loop."""

    max_rounds: int = DEFAULT_MAX_ROUNDS
    plan_only: bool = False
    is_cancelled: CancelCheck | None = None


@dataclass(frozen=True)
class AgentUsage:
    """Aggregated provider token usage for one agent loop run."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    cache_read_input_tokens: int | None = None

    def add(self, usage: ProviderUsage | None) -> "AgentUsage":
        if usage is None:
            return self
        return AgentUsage(
            input_tokens=_sum_optional_int(self.input_tokens, usage.input_tokens),
            output_tokens=_sum_optional_int(self.output_tokens, usage.output_tokens),
            cache_creation_input_tokens=_sum_optional_int(self.cache_creation_input_tokens, usage.cache_creation_input_tokens),
            cache_read_input_tokens=_sum_optional_int(self.cache_read_input_tokens, usage.cache_read_input_tokens),
        )

    def to_subagent_usage(self):  # type: ignore[no-untyped-def]
        from miniclaudecode.subagents.core import SubAgentUsage

        return SubAgentUsage(
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cache_creation_input_tokens=self.cache_creation_input_tokens,
            cache_read_input_tokens=self.cache_read_input_tokens,
        )


@dataclass
class AgentState:
    """Observable state for one agent loop run."""

    round_index: int = 0
    stop_reason: str | None = None
    final_text: str = ""
    usage: AgentUsage = field(default_factory=AgentUsage)


@dataclass(frozen=True)
class AgentTextDelta:
    """A streamed model text delta from one agent round."""

    text: str
    round_index: int


@dataclass(frozen=True)
class AgentThinking:
    """A placeholder event for future provider thinking deltas."""

    round_index: int
    text: str | None = None


@dataclass(frozen=True)
class AgentToolCallStarted:
    """A model-requested tool call is about to be handled."""

    call: ToolCall
    round_index: int


@dataclass(frozen=True)
class AgentToolResultEvent:
    """A tool call produced a structured result."""

    result: ToolResult
    round_index: int


@dataclass(frozen=True)
class AgentToolResultOffloaded:
    """A tool result was saved as a local context artifact."""

    offload: ToolResultOffloadInfo
    round_index: int


@dataclass(frozen=True)
class AgentContextManagementEvent:
    """A context-management action produced a user-visible result."""

    result: ContextManagementResult
    automatic: bool


@dataclass(frozen=True)
class AgentHookDiagnosticEvent:
    """A Hook rule produced a user-visible diagnostic."""

    diagnostic: HookDiagnostic


@dataclass(frozen=True)
class AgentFinalResponse:
    """The agent loop ended with a final assistant response."""

    text: str
    reason: str = "final"


@dataclass(frozen=True)
class AgentError:
    """The agent loop ended because of an error."""

    message: str


@dataclass(frozen=True)
class AgentCancelled:
    """The agent loop was cancelled by the caller."""

    message: str = "本轮生成已取消。"


@dataclass(frozen=True)
class AgentMaxRoundsReached:
    """The agent loop stopped because its model-call limit was reached."""

    max_rounds: int
    message: str


AgentEvent = (
    AgentTextDelta
    | AgentThinking
    | AgentToolCallStarted
    | AgentToolResultEvent
    | AgentToolResultOffloaded
    | AgentContextManagementEvent
    | AgentHookDiagnosticEvent
    | AgentFinalResponse
    | AgentError
    | AgentCancelled
    | AgentMaxRoundsReached
)


@dataclass
class AgentCallbacks:
    """Compatibility callbacks used by UI adapters while an agent turn runs."""

    on_text_delta: TextCallback | None = None
    on_tool_call_started: ToolCallback | None = None
    on_tool_call_finished: ToolResultCallback | None = None
    on_status: StatusCallback | None = None
    confirm_tool: ConfirmTool | None = None


@dataclass(frozen=True)
class _PreparedToolCall:
    call: ToolCall
    tool: Tool


class AgentRunner:
    """Run one user turn as a finite ReAct loop."""

    def __init__(
        self,
        provider: BaseProvider,
        session: Session,
        registry: ToolRegistry,
        context: ToolContext,
        callbacks: AgentCallbacks | None = None,
        options: AgentOptions | None = None,
        prompt_assembler: PromptAssembler | None = None,
        runtime_context_factory: RuntimeContextFactory | None = None,
        policy_engine: PolicyEngine | None = None,
        policy_context: PolicyContext | None = None,
        context_manager: ContextManager | None = None,
        skill_runtime: SkillRuntime | None = None,
        generation_watch_config: GenerationWatchConfig | None = None,
        hook_engine: HookEngine | None = None,
        dynamic_instructions: Sequence[DynamicInstruction] = (),
    ) -> None:
        self.provider = provider
        self.session = session
        self.registry = registry
        self.context = context
        self.callbacks = callbacks or AgentCallbacks()
        self.options = options or AgentOptions()
        self.prompt_assembler = prompt_assembler
        self.runtime_context_factory = runtime_context_factory
        self.policy_engine = policy_engine or PolicyEngine()
        self.policy_context = policy_context or PolicyContext(workdir=self.context.workdir, plan_only=self.options.plan_only)
        self.context_manager = context_manager
        self.skill_runtime = skill_runtime
        self.generation_watch_config = generation_watch_config or GenerationWatchConfig()
        self.hook_engine = hook_engine
        self.dynamic_instructions = tuple(dynamic_instructions)
        self._current_generation_watchdog: GenerationWatchdog | None = None
        if self.options.max_rounds < 1:
            raise ValueError("AgentOptions.max_rounds must be at least 1.")
        self.state = AgentState()

    def stream(self, history: Sequence[Message]) -> Generator[AgentEvent, None, None]:
        """Stream agent events until the current user request terminates."""

        try:
            for round_index in range(1, self.options.max_rounds + 1):
                self.state.round_index = round_index
                if self._is_cancelled():
                    yield self._cancelled_event()
                    return

                yield from self._trigger_hook_event(
                    HookEventName.AGENT_TURN_START,
                    HookContext(event=HookEventName.AGENT_TURN_START, workdir=self.context.workdir, round_index=round_index),
                )

                turn = yield from self._stream_provider_turn(
                    history if round_index == 1 else self.session.get_history(),
                    self._visible_tools(),
                    round_index,
                    f"Generating... (round {round_index}/{self.options.max_rounds})",
                )
                if self._is_cancelled():
                    yield self._cancelled_event()
                    return

                if not turn.tool_calls:
                    self.session.add_assistant_message(turn.text)
                    self.state.stop_reason = "final"
                    self.state.final_text = turn.text
                    yield from self._trigger_hook_event(
                        HookEventName.AGENT_TURN_END,
                        HookContext(event=HookEventName.AGENT_TURN_END, workdir=self.context.workdir, round_index=round_index),
                    )
                    yield AgentFinalResponse(turn.text)
                    return

                if round_index >= self.options.max_rounds:
                    final_text = yield from self._finish_after_max_rounds(round_index)
                    yield from self._trigger_hook_event(
                        HookEventName.AGENT_TURN_END,
                        HookContext(event=HookEventName.AGENT_TURN_END, workdir=self.context.workdir, round_index=round_index),
                    )
                    yield AgentFinalResponse(final_text, reason="max_rounds")
                    return

                results = yield from self._execute_tool_calls(turn.tool_calls, round_index)
                if results is None:
                    yield self._cancelled_event()
                    return
                self.session.add_assistant_tool_calls(turn.text, turn.tool_calls)
                tool_messages = self.session.add_tool_results(results)
                for message in tool_messages:
                    if message.tool_result_offload is not None:
                        yield AgentToolResultOffloaded(message.tool_result_offload, round_index)
                yield from self._trigger_hook_event(
                    HookEventName.AGENT_TURN_END,
                    HookContext(event=HookEventName.AGENT_TURN_END, workdir=self.context.workdir, round_index=round_index),
                )

            final_text = yield from self._finish_after_max_rounds(self.options.max_rounds)
            yield AgentFinalResponse(final_text, reason="max_rounds")
        except ProviderError as exc:
            message = f"错误：{exc} 本轮 assistant 回复未保存到历史。"
            self.state.stop_reason = "error"
            yield AgentError(message)
        except Exception:
            message = "错误：工具调用流程失败。本轮 assistant 回复未保存到历史。"
            self.state.stop_reason = "error"
            yield AgentError(message)

    def run(self, history: Sequence[Message]) -> str:
        """Run the agent loop to completion and return the final text."""

        final_text = ""
        for event in self.stream(history):
            if isinstance(event, AgentFinalResponse):
                final_text = event.text
        return final_text

    def poll_generation_watchdog(self) -> list[GenerationWatchEvent]:
        """Return model-generation observation events for the active provider turn."""

        if self._current_generation_watchdog is None:
            return []
        return self._current_generation_watchdog.poll()

    def _finish_after_max_rounds(self, round_index: int) -> Generator[AgentEvent, None, str]:
        notice = f"已达到最大轮数 {self.options.max_rounds}，未继续执行更多工具。"
        self.state.stop_reason = "max_rounds"
        yield AgentMaxRoundsReached(self.options.max_rounds, notice)
        if self._is_cancelled():
            return notice

        synthesis_turn = yield from self._stream_provider_turn(
            self.session.get_history(),
            [],
            round_index + 1,
            "Summarizing after max rounds...",
            dynamic_instructions=[
                DynamicInstruction(name="max_rounds_synthesis", content=_max_rounds_synthesis_prompt())
            ],
        )
        synthesis_text = synthesis_turn.text.strip() or INSUFFICIENT_INFORMATION_MESSAGE
        final_text = f"{notice}\n\n{synthesis_text}"
        self.session.add_assistant_message(final_text)
        self.state.final_text = final_text
        return final_text

    def _stream_provider_turn(
        self,
        history: Sequence[Message],
        tools: list[Tool],
        round_index: int,
        status: str,
        dynamic_instructions: Sequence[DynamicInstruction] = (),
    ) -> Generator[AgentEvent, None, ProviderTurnComplete]:
        self._emit_status(status)
        last_complete: ProviderTurnComplete | None = None
        prompt = self._assemble_prompt(dynamic_instructions)
        request_history = list(history)
        if self.context_manager is not None:
            context_result = self.context_manager.prepare_before_request(
                session=self.session,
                provider=self.provider,
                tools=tools,
                prompt=prompt,
                allow_auto_compact=True,
            )
            if _should_emit_context_management_event(context_result):
                yield AgentContextManagementEvent(context_result, automatic=True)
            if context_result.compacted:
                yield from self._trigger_hook_event(
                    HookEventName.COMPACT,
                    HookContext(
                        event=HookEventName.COMPACT,
                        workdir=self.context.workdir,
                        round_index=round_index,
                        extra={"compact": {"reason": context_result.reason}},
                    ),
                )
            request_history = self.session.get_history()
        self._current_generation_watchdog = GenerationWatchdog(
            self.generation_watch_config,
            phase="agent_round",
            label=f"Agent 第 {round_index} 轮模型生成",
            round_index=round_index,
        )
        try:
            stream = (
                self.provider.stream_turn(request_history, tools=tools, prompt=prompt)
                if prompt is not None
                else self.provider.stream_turn(request_history, tools=tools)
            )
            for event in stream:
                if isinstance(event, ProviderTextDelta):
                    if self._current_generation_watchdog is not None:
                        self._current_generation_watchdog.mark_activity()
                    agent_event = AgentTextDelta(event.text, round_index)
                    self._emit_text_delta(event.text)
                    yield agent_event
                    if self._is_cancelled():
                        return ProviderTurnComplete(text="")
                elif isinstance(event, ProviderTurnComplete):
                    if self._current_generation_watchdog is not None:
                        self._current_generation_watchdog.mark_activity()
                    self.state.usage = self.state.usage.add(event.usage)
                    last_complete = event
            if last_complete is None:
                return ProviderTurnComplete(text="")
            return last_complete
        finally:
            if self._current_generation_watchdog is not None:
                self._current_generation_watchdog.finish()
            self._current_generation_watchdog = None

    def _assemble_prompt(self, dynamic_instructions: Sequence[DynamicInstruction] = ()):
        if self.prompt_assembler is None:
            return None
        instructions = [*self.dynamic_instructions, *dynamic_instructions]
        if self.skill_runtime is not None:
            instructions.extend(self.skill_runtime.dynamic_instructions())
        if self.hook_engine is not None:
            instructions.extend(self.hook_engine.consume_dynamic_instructions())
        if self.options.plan_only:
            instructions.append(plan_only_instruction())
        runtime_context = self.runtime_context_factory() if self.runtime_context_factory is not None else None
        return self.prompt_assembler.assemble(
            runtime_context=runtime_context,
            dynamic_instructions=instructions,
        )

    def _visible_tools(self) -> list[Tool]:
        tools = self.registry.list_tools()
        if self.skill_runtime is None:
            return tools
        return self.skill_runtime.visible_tools(tools)

    def _execute_tool_calls(
        self,
        calls: list[ToolCall],
        round_index: int,
    ) -> Generator[AgentEvent, None, list[ToolResult] | None]:
        results: list[ToolResult] = []
        parallel_batch: list[_PreparedToolCall] = []
        for index, call in enumerate(calls):
            if self._is_cancelled():
                if results or parallel_batch:
                    results.extend(_cancel_unfinished_outcomes(parallel_batch, calls[index:]))
                    return results
                return None

            if parallel_batch and not self._call_may_be_parallel_safe(call):
                for result in _run_async_tool_batch(parallel_batch, self.context):
                    results.append(result)
                    yield from self._tool_result_events(result, round_index)
                parallel_batch = []
                if self._is_cancelled():
                    results.extend(_cancelled_tool_results(calls[index:]))
                    return results

            outcome = yield from self._prepare_tool_call_events(call, round_index)
            if self._is_cancelled():
                results.extend(_cancel_unfinished_outcomes([*parallel_batch, outcome], calls[index + 1 :]))
                return results

            if isinstance(outcome, ToolResult):
                if parallel_batch:
                    for result in _run_async_tool_batch(parallel_batch, self.context):
                        results.append(result)
                        yield from self._tool_result_events(result, round_index)
                    parallel_batch = []
                results.append(outcome)
                yield from self._tool_result_events(outcome, round_index)
                continue

            if _is_parallel_safe_tool(outcome.tool):
                parallel_batch.append(outcome)
                continue

            result = _execute_prepared_tool_call(outcome, self.context)
            results.append(result)
            yield from self._tool_result_events(result, round_index)

        if parallel_batch:
            for result in _run_async_tool_batch(parallel_batch, self.context):
                results.append(result)
                yield from self._tool_result_events(result, round_index)

        return results

    def _prepare_tool_call_events(
        self,
        call: ToolCall,
        round_index: int,
    ) -> Generator[AgentEvent, None, ToolResult | _PreparedToolCall]:
        if call.parse_error:
            result = ToolResult.fail(
                tool_name=call.name or "unknown",
                error=call.parse_error,
                tool_call_id=call.id,
            )
            return result
        try:
            tool = self.registry.get(call.name)
        except ToolRegistryError as exc:
            result = ToolResult.fail(tool_name=call.name or "unknown", error=str(exc), tool_call_id=call.id)
            return result

        started = AgentToolCallStarted(call, round_index)
        self._emit_tool_started(call)
        yield started
        self._emit_status(f"Calling tool: {call.name}")

        if self.options.plan_only and tool.name not in PLAN_ONLY_READ_TOOLS:
            result = ToolResult.fail(
                tool_name=tool.name,
                error=f"plan-only 模式下禁止执行 {tool.name}，真实操作未执行。",
                metadata={"blocked_by": "plan-only"},
                tool_call_id=call.id,
            )
            return result

        decision = self.policy_engine.evaluate(call, tool, self.context, self.policy_context)
        if decision.action is PolicyAction.DENY:
            result = ToolResult.fail(
                tool_name=tool.name,
                error=decision.reason,
                metadata=_policy_metadata(decision),
                tool_call_id=call.id,
            )
            return result

        if decision.action is PolicyAction.ASK:
            self._emit_status(f"Waiting confirmation: {call.name}")
            confirmation = self._confirm_policy_decision(call, tool, decision)
            if self._is_cancelled():
                result = ToolResult.fail(
                    tool_name=tool.name,
                    error="用户取消了本轮请求，真实操作未执行。",
                    metadata={"cancelled": True},
                    tool_call_id=call.id,
                )
                return result
            if confirmation is ConfirmationResponse.DENY:
                result = ToolResult.fail(
                    tool_name=tool.name,
                    error="用户拒绝执行该工具，真实操作未执行。",
                    metadata={"blocked_by": "user"},
                    tool_call_id=call.id,
                )
                return result
            if confirmation is ConfirmationResponse.ALLOW_SESSION:
                self.policy_context.session_policy.add_allow_for_call(call, tool, self.context)

        if self._is_cancelled():
            result = ToolResult.fail(
                tool_name=tool.name,
                error="用户取消了本轮请求，真实操作未执行。",
                metadata={"cancelled": True},
                tool_call_id=call.id,
            )
            return result

        if self.hook_engine is not None:
            hook_result = self.hook_engine.trigger_tool_before(
                HookContext(
                    event=HookEventName.TOOL_BEFORE_EXECUTE,
                    workdir=self.context.workdir,
                    tool={"name": tool.name, "arguments": call.arguments, "id": call.id},
                    tool_call=call,
                    round_index=round_index,
                )
            )
            for diagnostic in hook_result.diagnostics:
                yield AgentHookDiagnosticEvent(diagnostic)
            if hook_result.intercept is not None and hook_result.intercept.blocked:
                reason = hook_result.intercept.reason or "Hook 规则拦截了该工具调用。"
                result = ToolResult.fail(
                    tool_name=tool.name,
                    error=f"Hook 拦截：{reason}",
                    metadata={"blocked_by": "hook", "hook_rules": list(hook_result.intercept.rule_names)},
                    tool_call_id=call.id,
                )
                return result

        return _PreparedToolCall(call=call, tool=tool)

    def _call_may_be_parallel_safe(self, call: ToolCall) -> bool:
        if call.parse_error:
            return False
        try:
            tool = self.registry.get(call.name)
        except ToolRegistryError:
            return False
        return _is_parallel_safe_tool(tool)

    def _confirm_policy_decision(
        self,
        call: ToolCall,
        tool: Tool,
        decision: PolicyDecision,
    ) -> ConfirmationResponse:
        if self.callbacks.confirm_tool is None:
            return ConfirmationResponse.DENY
        try:
            raw = self.callbacks.confirm_tool(call, tool, decision)
        except TypeError:
            raw = self.callbacks.confirm_tool(call, tool)
        return _normalize_confirmation_response(raw)

    def _tool_result_events(self, result: ToolResult, round_index: int) -> Generator[AgentEvent, None, None]:
        if self.hook_engine is not None:
            hook_result = self.hook_engine.trigger(
                HookEventName.TOOL_AFTER_EXECUTE,
                HookContext(
                    event=HookEventName.TOOL_AFTER_EXECUTE,
                    workdir=self.context.workdir,
                    tool={"name": result.tool_name},
                    tool_result=result,
                    round_index=round_index,
                ),
            )
            for diagnostic in hook_result.diagnostics:
                yield AgentHookDiagnosticEvent(diagnostic)
        self._emit_tool_result(result)
        yield AgentToolResultEvent(result, round_index)

    def _trigger_hook_event(
        self,
        event: HookEventName,
        context: HookContext,
    ) -> Generator[AgentEvent, None, None]:
        if self.hook_engine is None:
            return
        hook_result = self.hook_engine.trigger(event, context)
        for diagnostic in hook_result.diagnostics:
            yield AgentHookDiagnosticEvent(diagnostic)

    def _is_cancelled(self) -> bool:
        return self.options.is_cancelled() if self.options.is_cancelled is not None else False

    def _cancelled_event(self) -> AgentCancelled:
        self.state.stop_reason = "cancelled"
        return AgentCancelled()

    def _emit_text_delta(self, delta: str) -> None:
        if self.callbacks.on_text_delta is not None:
            self.callbacks.on_text_delta(delta)

    def _emit_status(self, status: str) -> None:
        if self.callbacks.on_status is not None:
            self.callbacks.on_status(status)

    def _emit_tool_started(self, call: ToolCall) -> None:
        if self.callbacks.on_tool_call_started is not None:
            self.callbacks.on_tool_call_started(call)

    def _emit_tool_result(self, result: ToolResult) -> None:
        if self.callbacks.on_tool_call_finished is not None:
            self.callbacks.on_tool_call_finished(result)


def _sum_optional_int(left: int | None, right: int | None) -> int | None:
    if right is None:
        return left
    if left is None:
        return right
    return left + right


def _should_emit_context_management_event(result: ContextManagementResult) -> bool:
    return result.compacted or result.error is not None or result.reason in {"summary_failed", "auto_compact_fused"}


def _normalize_confirmation_response(raw: bool | ConfirmationResponse | str) -> ConfirmationResponse:
    if raw is True:
        return ConfirmationResponse.ALLOW_ONCE
    if raw is False or raw is None:
        return ConfirmationResponse.DENY
    if isinstance(raw, ConfirmationResponse):
        return raw
    try:
        return ConfirmationResponse(str(raw))
    except ValueError:
        return ConfirmationResponse.DENY


def _policy_metadata(decision: PolicyDecision) -> dict[str, object]:
    metadata: dict[str, object] = {
        "blocked_by": "policy",
        "policy_action": decision.action.value,
        "policy_source": decision.source,
        "policy_reason": decision.reason,
    }
    if decision.matched_rule is not None:
        metadata["policy_rule"] = decision.matched_rule
    metadata.update(decision.metadata)
    return metadata


def _is_parallel_safe_tool(tool: Tool) -> bool:
    return tool.name in PARALLEL_SAFE_LOCAL_TOOLS or getattr(tool, "parallel_safe", False) is True


def _execute_prepared_tool_call(prepared: _PreparedToolCall, context: ToolContext) -> ToolResult:
    try:
        result = prepared.tool.execute(prepared.call.arguments, context)
    except Exception as exc:
        return ToolResult.fail(
            tool_name=prepared.tool.name,
            error=f"工具执行失败：{exc}",
            tool_call_id=prepared.call.id,
        )
    return _with_tool_call_id(result, prepared.call.id)


def _with_tool_call_id(result: ToolResult, tool_call_id: str) -> ToolResult:
    if result.tool_call_id is not None:
        return result
    return ToolResult(
        tool_name=result.tool_name,
        success=result.success,
        content=result.content,
        error=result.error,
        metadata=result.metadata,
        tool_call_id=tool_call_id,
    )


def _run_async_tool_batch(batch: Sequence[_PreparedToolCall], context: ToolContext) -> list[ToolResult]:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_execute_async_tool_batch(batch, context))

    result_holder: dict[str, list[ToolResult]] = {}
    error_holder: dict[str, BaseException] = {}

    def run() -> None:
        try:
            result_holder["results"] = asyncio.run(_execute_async_tool_batch(batch, context))
        except BaseException as exc:
            error_holder["error"] = exc

    thread = threading.Thread(target=run)
    thread.start()
    thread.join()
    if "error" in error_holder:
        raise error_holder["error"]
    return result_holder["results"]


async def _execute_async_tool_batch(batch: Sequence[_PreparedToolCall], context: ToolContext) -> list[ToolResult]:
    raw_results = await asyncio.gather(
        *(asyncio.to_thread(_execute_prepared_tool_call, prepared, context) for prepared in batch),
        return_exceptions=True,
    )
    results: list[ToolResult] = []
    for prepared, raw_result in zip(batch, raw_results, strict=True):
        if isinstance(raw_result, ToolResult):
            results.append(raw_result)
        else:
            results.append(
                ToolResult.fail(
                    tool_name=prepared.tool.name,
                    error=f"工具执行失败：{raw_result}",
                    tool_call_id=prepared.call.id,
                )
            )
    return results


def _cancel_unfinished_outcomes(
    outcomes: Sequence[ToolResult | _PreparedToolCall],
    remaining_calls: Iterable[ToolCall],
) -> list[ToolResult]:
    results: list[ToolResult] = []
    for outcome in outcomes:
        if isinstance(outcome, ToolResult):
            results.append(outcome)
        else:
            results.append(
                ToolResult.fail(
                    tool_name=outcome.tool.name,
                    error="用户取消了本轮请求，真实操作未执行。",
                    metadata={"cancelled": True},
                    tool_call_id=outcome.call.id,
                )
            )
    results.extend(_cancelled_tool_results(remaining_calls))
    return results


def _cancelled_tool_results(calls: Iterable[ToolCall]) -> list[ToolResult]:
    return [
        ToolResult.fail(
            tool_name=call.name or "unknown",
            error="用户取消了本轮请求，真实操作未执行。",
            metadata={"cancelled": True},
            tool_call_id=call.id,
        )
        for call in calls
    ]


def _max_rounds_synthesis_prompt() -> str:
    return (
        "Agent Loop 已达到最大工具轮数，工具现在不可用。\n"
        "请只根据本轮对话中已经完成的工具结果和已有上下文回答用户最初的问题。\n"
        "如果已有信息足够，请总结已有信息并直接回答。\n"
        "如果已有信息不足，请明确说明：当前信息不足以回答该问题，并简短说明缺少什么信息。\n"
        "不要请求或描述新的工具调用，不要编造未观察到的信息。"
    )
