import threading
import time
import unittest
from collections.abc import Iterator, Mapping, Sequence
from typing import Any
from pathlib import Path
from tempfile import TemporaryDirectory

from miniclaudecode.agent import (
    AgentContextManagementEvent,
    AgentFinalResponse,
    AgentHookDiagnosticEvent,
    AgentMaxRoundsReached,
    AgentOptions,
    AgentRunner,
    AgentTextDelta,
    AgentToolCallStarted,
    AgentToolResultEvent,
    AgentToolResultOffloaded,
)
from miniclaudecode.context import ContextManagementConfig, ContextManager
from miniclaudecode.hooks import HookEngine, HookLoader, HookRegistry
from miniclaudecode.observability import GenerationWatchConfig
from miniclaudecode.prompt_modules import default_prompt_modules
from miniclaudecode.prompting import PromptAssembler, RuntimeContext
from miniclaudecode.providers.base import BaseProvider, ProviderEvent, ProviderTextDelta, ProviderTurnComplete
from miniclaudecode.session import Message, Session
from miniclaudecode.tools.base import Tool, ToolCall, ToolContext, ToolResult
from miniclaudecode.tools.defaults import create_default_tool_registry
from miniclaudecode.tools.registry import ToolRegistry


class ScriptedProvider(BaseProvider):
    def __init__(self, turns: list[list[ProviderEvent]]) -> None:
        self.turns = turns
        self.requests: list[list[Message]] = []
        self.tool_counts: list[int] = []
        self.prompts = []

    def stream_chat(self, messages: Sequence[Message]) -> Iterator[str]:
        for event in self.stream_turn(messages):
            if isinstance(event, ProviderTextDelta):
                yield event.text

    def stream_turn(self, messages: Sequence[Message], tools=None, prompt=None):  # type: ignore[no-untyped-def]
        self.requests.append(list(messages))
        self.tool_counts.append(0 if tools is None else len(tools))
        self.prompts.append(prompt)
        yield from self.turns.pop(0)


class BlockingProvider(BaseProvider):
    def __init__(self, *, hold_after_delta: bool = False) -> None:
        self.release = threading.Event()
        self.after_delta_release = threading.Event()
        self.hold_after_delta = hold_after_delta

    def stream_chat(self, messages: Sequence[Message]) -> Iterator[str]:
        return iter(())

    def stream_turn(self, messages: Sequence[Message], tools=None, prompt=None):  # type: ignore[no-untyped-def]
        self.release.wait(timeout=5)
        yield ProviderTextDelta("done")
        if self.hold_after_delta:
            self.after_delta_release.wait(timeout=5)
        yield ProviderTurnComplete(text="done")


class CancellingProvider(BaseProvider):
    def __init__(self) -> None:
        self.cancelled = False

    def stream_chat(self, messages: Sequence[Message]) -> Iterator[str]:
        return iter(())

    def stream_turn(self, messages: Sequence[Message], tools=None):  # type: ignore[no-untyped-def]
        yield ProviderTextDelta("partial")
        self.cancelled = True
        yield ProviderTurnComplete(text="partial")


class TimedTool(Tool):
    description = "Timed tool."
    input_schema = {"type": "object", "properties": {}}
    requires_confirmation = False

    def __init__(self, name: str, recorder: list[tuple[str, str, float]], *, parallel_safe: bool) -> None:
        self.name = name
        self.parallel_safe = parallel_safe
        self._recorder = recorder

    def execute(self, arguments: Mapping[str, Any], context: ToolContext) -> ToolResult:
        _ = context
        delay = float(arguments.get("delay", 0.05))
        with _RECORDER_LOCK:
            self._recorder.append((self.name, "start", time.perf_counter()))
        time.sleep(delay)
        with _RECORDER_LOCK:
            self._recorder.append((self.name, "end", time.perf_counter()))
        return ToolResult.ok(tool_name=self.name, content=f"done {self.name}")


class FailingTool(Tool):
    name = "safe_fail"
    description = "Failing safe tool."
    input_schema = {"type": "object", "properties": {}}
    requires_confirmation = False
    parallel_safe = True

    def execute(self, arguments: Mapping[str, Any], context: ToolContext) -> ToolResult:
        _ = arguments, context
        raise RuntimeError("boom")


class RecordingTool(Tool):
    name = "record"
    description = "Recording tool."
    input_schema = {"type": "object", "properties": {}, "additionalProperties": False}
    requires_confirmation = False

    def __init__(self) -> None:
        self.calls = 0

    def execute(self, arguments: Mapping[str, Any], context: ToolContext) -> ToolResult:
        _ = arguments, context
        self.calls += 1
        return ToolResult.ok(tool_name=self.name, content="recorded")


_RECORDER_LOCK = threading.Lock()


def _hook_engine(root: Path) -> HookEngine:
    registry = HookRegistry(HookLoader(project_root=root / "hooks"))
    registry.load_initial()
    return HookEngine(registry, tool_context=ToolContext(root))


class AgentLoopTests(unittest.TestCase):
    def test_agent_generation_watchdog_reports_idle_and_total_without_changing_final_response(self) -> None:
        provider = BlockingProvider()
        session = Session()
        session.add_user_message("slow")
        runner = AgentRunner(
            provider,
            session,
            create_default_tool_registry(),
            ToolContext(Path.cwd()),
            generation_watch_config=GenerationWatchConfig(idle_timeout_seconds=0.05, total_timeout_seconds=0.05),
        )
        events_holder: list[object] = []

        thread = threading.Thread(target=lambda: events_holder.extend(runner.stream(session.get_history())))
        thread.start()
        time.sleep(0.12)

        watch_events = runner.poll_generation_watchdog()
        provider.release.set()
        thread.join(timeout=5)

        self.assertIn("idle_timeout", [event.kind for event in watch_events])
        self.assertIn("total_timeout", [event.kind for event in watch_events])
        self.assertIsInstance(events_holder[-1], AgentFinalResponse)
        self.assertEqual(session.get_history()[-1].content, "done")

    def test_agent_generation_watchdog_reports_recovered_after_activity(self) -> None:
        provider = BlockingProvider(hold_after_delta=True)
        session = Session()
        session.add_user_message("slow")
        runner = AgentRunner(
            provider,
            session,
            create_default_tool_registry(),
            ToolContext(Path.cwd()),
            generation_watch_config=GenerationWatchConfig(idle_timeout_seconds=0.05, total_timeout_seconds=10),
        )
        events_holder: list[object] = []

        thread = threading.Thread(target=lambda: events_holder.extend(runner.stream(session.get_history())))
        thread.start()
        time.sleep(0.12)
        runner.poll_generation_watchdog()
        provider.release.set()
        time.sleep(0.05)
        watch_events = runner.poll_generation_watchdog()
        provider.after_delta_release.set()
        thread.join(timeout=5)

        self.assertIn("recovered", [event.kind for event in watch_events])
        self.assertIsInstance(events_holder[-1], AgentFinalResponse)

    def test_glob_then_read_then_final_answer(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "note.txt").write_text("王勇大神", encoding="utf-8")
            provider = ScriptedProvider(
                [
                    [ProviderTurnComplete(text="", tool_calls=[ToolCall(id="call-1", name="glob_files", arguments={"pattern": "**/note.txt"})])],
                    [ProviderTurnComplete(text="", tool_calls=[ToolCall(id="call-2", name="read_file", arguments={"path": "note.txt"})])],
                    [ProviderTextDelta("note 内容是王勇大神"), ProviderTurnComplete(text="note 内容是王勇大神")],
                ]
            )
            session = Session()
            session.add_user_message("查看 note")
            runner = AgentRunner(provider, session, create_default_tool_registry(), ToolContext(root))

            events = list(runner.stream(session.get_history()))

        self.assertEqual(len(provider.requests), 3)
        self.assertEqual(provider.tool_counts, [6, 6, 6])
        self.assertTrue(any(message.role == "tool" and "note.txt" in message.content for message in provider.requests[1]))
        self.assertTrue(any(message.role == "tool" and "王勇大神" in message.content for message in provider.requests[2]))
        self.assertIsInstance(events[-1], AgentFinalResponse)
        self.assertEqual(session.get_history()[-1], Message(role="assistant", content="note 内容是王勇大神"))

    def test_max_rounds_stops_before_executing_tools_from_limit_round(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "note.txt").write_text("loop", encoding="utf-8")
            turns = [
                [ProviderTurnComplete(text="", tool_calls=[ToolCall(id=f"call-{index}", name="read_file", arguments={"path": "note.txt"})])]
                for index in range(1, 21)
            ]
            turns.append([ProviderTurnComplete(text="已有信息足够：note.txt 内容是 loop。")])
            provider = ScriptedProvider(turns)
            session = Session()
            session.add_user_message("keep reading")
            runner = AgentRunner(provider, session, create_default_tool_registry(), ToolContext(root))

            events = list(runner.stream(session.get_history()))

        self.assertEqual(len(provider.requests), 21)
        self.assertEqual(provider.tool_counts, [*([6] * 20), 0])
        self.assertEqual(len([message for message in session.get_history() if message.role == "tool"]), 19)
        self.assertTrue(any(isinstance(event, AgentMaxRoundsReached) for event in events))
        self.assertIn("最大轮数", session.get_history()[-1].content)
        self.assertIn("已有信息足够", session.get_history()[-1].content)

    def test_max_rounds_synthesis_can_report_insufficient_information(self) -> None:
        turns = [
            [ProviderTurnComplete(text="", tool_calls=[ToolCall(id=f"call-{index}", name="glob_files", arguments={"pattern": "missing-{index}.txt"})])]
            for index in range(1, 21)
        ]
        turns.append([ProviderTurnComplete(text="当前信息不足以回答该问题，缺少目标文件内容。")])
        provider = ScriptedProvider(turns)
        session = Session()
        session.add_user_message("read missing file")
        runner = AgentRunner(provider, session, create_default_tool_registry(), ToolContext(Path.cwd()))

        final = runner.run(session.get_history())

        self.assertEqual(provider.tool_counts[-1], 0)
        self.assertIn("最大轮数", final)
        self.assertIn("不足以回答", final)

    def test_max_rounds_synthesis_uses_dynamic_instruction_not_user_message(self) -> None:
        turns = [
            [ProviderTurnComplete(text="", tool_calls=[ToolCall(id=f"call-{index}", name="glob_files", arguments={"pattern": "*.txt"})])]
            for index in range(1, 21)
        ]
        turns.append([ProviderTurnComplete(text="当前信息不足以回答该问题。")])
        provider = ScriptedProvider(turns)
        session = Session()
        session.add_user_message("keep searching")
        runner = AgentRunner(
            provider,
            session,
            create_default_tool_registry(),
            ToolContext(Path.cwd()),
            prompt_assembler=PromptAssembler(default_prompt_modules()),
            runtime_context_factory=lambda: RuntimeContext(
                workdir=Path.cwd(),
                os_name="test-os",
                current_date="2026-06-14",
                provider_name="fake",
                model="fake-model",
            ),
        )

        runner.run(session.get_history())

        self.assertEqual(provider.requests[-1], session.get_history()[:-1])
        self.assertFalse(any("Agent Loop 已达到最大工具轮数" in message.content for message in provider.requests[-1]))
        self.assertIsNotNone(provider.prompts[-1])
        prompt_text = provider.prompts[-1].render_text()
        self.assertIn("max_rounds_synthesis", prompt_text)
        self.assertIn("Agent Loop 已达到最大工具轮数", prompt_text)

    def test_max_rounds_synthesis_ignores_unexpected_tool_calls(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "late.txt"
            turns = [
                [ProviderTurnComplete(text="", tool_calls=[ToolCall(id=f"call-{index}", name="glob_files", arguments={"pattern": "*.txt"})])]
                for index in range(1, 21)
            ]
            turns.append([
                ProviderTurnComplete(
                    text="当前信息不足以回答该问题。",
                    tool_calls=[ToolCall(id="call-late", name="write_file", arguments={"path": "late.txt", "content": "x"})],
                )
            ])
            provider = ScriptedProvider(turns)
            session = Session()
            session.add_user_message("try late tool")
            runner = AgentRunner(provider, session, create_default_tool_registry(), ToolContext(root))

            final = runner.run(session.get_history())

        self.assertFalse(target.exists())
        self.assertIn("不足以回答", final)
        self.assertFalse(any(message.role == "assistant" and message.tool_calls for message in session.get_history()[-1:]))

    def test_multiple_tools_in_one_round_emit_ordered_events(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.txt").write_text("A", encoding="utf-8")
            (root / "b.txt").write_text("B", encoding="utf-8")
            provider = ScriptedProvider(
                [
                    [
                        ProviderTurnComplete(
                            text="",
                            tool_calls=[
                                ToolCall(id="call-a", name="read_file", arguments={"path": "a.txt"}),
                                ToolCall(id="call-b", name="read_file", arguments={"path": "b.txt"}),
                            ],
                        )
                    ],
                    [ProviderTurnComplete(text="done")],
                ]
            )
            session = Session()
            session.add_user_message("read both")
            runner = AgentRunner(provider, session, create_default_tool_registry(), ToolContext(root))

            events = list(runner.stream(session.get_history()))

        tool_events = [event for event in events if isinstance(event, AgentToolCallStarted | AgentToolResultEvent)]
        self.assertEqual([type(event) for event in tool_events], [AgentToolCallStarted, AgentToolCallStarted, AgentToolResultEvent, AgentToolResultEvent])
        self.assertEqual(tool_events[0].call.id, "call-a")  # type: ignore[attr-defined]
        self.assertEqual(tool_events[1].call.id, "call-b")  # type: ignore[attr-defined]
        self.assertEqual(tool_events[2].result.tool_call_id, "call-a")  # type: ignore[attr-defined]
        self.assertEqual(tool_events[3].result.tool_call_id, "call-b")  # type: ignore[attr-defined]

    def test_parallel_safe_tools_run_concurrently_and_keep_result_order(self) -> None:
        recorder: list[tuple[str, str, float]] = []
        registry = ToolRegistry()
        registry.register(TimedTool("safe_a", recorder, parallel_safe=True))
        registry.register(TimedTool("safe_b", recorder, parallel_safe=True))
        provider = ScriptedProvider(
            [
                [
                    ProviderTurnComplete(
                        text="",
                        tool_calls=[
                            ToolCall(id="call-a", name="safe_a", arguments={"delay": 0.08}),
                            ToolCall(id="call-b", name="safe_b", arguments={"delay": 0.08}),
                        ],
                    )
                ],
                [ProviderTurnComplete(text="done")],
            ]
        )
        session = Session()
        session.add_user_message("run safe tools")
        runner = AgentRunner(provider, session, registry, ToolContext(Path.cwd()))

        events = list(runner.stream(session.get_history()))

        tool_results = [event.result for event in events if isinstance(event, AgentToolResultEvent)]
        self.assertEqual([result.tool_call_id for result in tool_results], ["call-a", "call-b"])
        starts = {name: timestamp for name, state, timestamp in recorder if state == "start"}
        ends = {name: timestamp for name, state, timestamp in recorder if state == "end"}
        self.assertLess(starts["safe_b"], ends["safe_a"])
        self.assertLess(starts["safe_a"], ends["safe_b"])

    def test_unsafe_tool_breaks_parallel_batches(self) -> None:
        recorder: list[tuple[str, str, float]] = []
        registry = ToolRegistry()
        registry.register(TimedTool("safe_a", recorder, parallel_safe=True))
        registry.register(TimedTool("unsafe", recorder, parallel_safe=False))
        registry.register(TimedTool("safe_b", recorder, parallel_safe=True))
        provider = ScriptedProvider(
            [
                [
                    ProviderTurnComplete(
                        text="",
                        tool_calls=[
                            ToolCall(id="call-a", name="safe_a", arguments={"delay": 0.01}),
                            ToolCall(id="call-u", name="unsafe", arguments={"delay": 0.04}),
                            ToolCall(id="call-b", name="safe_b", arguments={"delay": 0.01}),
                        ],
                    )
                ],
                [ProviderTurnComplete(text="done")],
            ]
        )
        session = Session()
        session.add_user_message("run mixed tools")
        runner = AgentRunner(provider, session, registry, ToolContext(Path.cwd()))

        runner.run(session.get_history())

        starts = {name: timestamp for name, state, timestamp in recorder if state == "start"}
        ends = {name: timestamp for name, state, timestamp in recorder if state == "end"}
        self.assertLess(ends["safe_a"], starts["unsafe"])
        self.assertLess(ends["unsafe"], starts["safe_b"])

    def test_parallel_batch_failure_keeps_other_results(self) -> None:
        recorder: list[tuple[str, str, float]] = []
        registry = ToolRegistry()
        registry.register(TimedTool("safe_ok", recorder, parallel_safe=True))
        registry.register(FailingTool())
        provider = ScriptedProvider(
            [
                [
                    ProviderTurnComplete(
                        text="",
                        tool_calls=[
                            ToolCall(id="call-ok", name="safe_ok", arguments={"delay": 0.01}),
                            ToolCall(id="call-fail", name="safe_fail", arguments={}),
                        ],
                    )
                ],
                [ProviderTurnComplete(text="done")],
            ]
        )
        session = Session()
        session.add_user_message("run safe tools")
        runner = AgentRunner(provider, session, registry, ToolContext(Path.cwd()))

        events = list(runner.stream(session.get_history()))

        tool_results = [event.result for event in events if isinstance(event, AgentToolResultEvent)]
        self.assertEqual([result.tool_call_id for result in tool_results], ["call-ok", "call-fail"])
        self.assertTrue(tool_results[0].success)
        self.assertFalse(tool_results[1].success)
        self.assertIn("boom", tool_results[1].error or "")

    def test_parse_error_and_unknown_tool_are_written_as_tool_results(self) -> None:
        provider = ScriptedProvider(
            [
                [
                    ProviderTurnComplete(
                        text="",
                        tool_calls=[
                            ToolCall(id="call-parse", name="read_file", parse_error="bad json"),
                            ToolCall(id="call-missing", name="missing_tool", arguments={}),
                        ],
                    )
                ],
                [ProviderTurnComplete(text="handled failures")],
            ]
        )
        session = Session()
        session.add_user_message("bad tools")
        runner = AgentRunner(provider, session, create_default_tool_registry(), ToolContext(Path.cwd()))

        final = runner.run(session.get_history())

        self.assertEqual(final, "handled failures")
        tool_messages = [message.content for message in session.get_history() if message.role == "tool"]
        self.assertEqual(len(tool_messages), 2)
        self.assertTrue(any("bad json" in content for content in tool_messages))
        self.assertTrue(any("missing_tool" in content for content in tool_messages))

    def test_cancel_during_provider_stream_does_not_save_partial_assistant(self) -> None:
        provider = CancellingProvider()
        session = Session()
        session.add_user_message("cancel")
        runner = AgentRunner(
            provider,
            session,
            create_default_tool_registry(),
            ToolContext(Path.cwd()),
            options=AgentOptions(is_cancelled=lambda: provider.cancelled),
        )

        events = list(runner.stream(session.get_history()))

        self.assertTrue(any(isinstance(event, AgentTextDelta) for event in events))
        self.assertEqual(session.get_history(), [Message(role="user", content="cancel")])

    def test_offloaded_tool_result_event_is_emitted_after_session_storage(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "large.txt").write_text("x" * 1000, encoding="utf-8")
            provider = ScriptedProvider(
                [
                    [ProviderTurnComplete(text="", tool_calls=[ToolCall(id="call-large", name="read_file", arguments={"path": "large.txt"})])],
                    [ProviderTurnComplete(text="done")],
                ]
            )
            context_manager = ContextManager(
                ContextManagementConfig(single_tool_result_token_limit=5, tool_result_preview_chars=20),
                workdir=root,
            )
            session = Session(tool_result_slimmer=context_manager.tool_result_slimmer)
            session.add_user_message("read large")
            runner = AgentRunner(
                provider,
                session,
                create_default_tool_registry(),
                ToolContext(root),
                context_manager=context_manager,
            )

            events = list(runner.stream(session.get_history()))

        offload_events = [event for event in events if isinstance(event, AgentToolResultOffloaded)]
        self.assertEqual(len(offload_events), 1)
        self.assertEqual(offload_events[0].offload.tool_name, "read_file")
        self.assertIn("tool-results", offload_events[0].offload.path)
        self.assertIsNotNone([message for message in session.get_history() if message.role == "tool"][0].tool_result_offload)

    def test_auto_compaction_event_is_emitted_when_prepare_compacts(self) -> None:
        provider = ScriptedProvider([[ProviderTurnComplete(text="done")]])
        provider.count_tokens = lambda messages, tools=None, prompt=None: 60  # type: ignore[method-assign]
        summary_provider_turn = ProviderTurnComplete(
            text=(
                "<formal_summary>\n"
                "## 主要请求\n继续开发。\n"
                "## 关键概念\n上下文管理。\n"
                "## 文件代码\n无。\n"
                "## 错误修复\n无。\n"
                "## 解决过程\n已压缩旧历史。\n"
                "## 用户原话\n无。\n"
                "## 待办\n无。\n"
                "## 当前工作\n无。\n"
                "## 下一步\n无。\n"
                "</formal_summary>"
            )
        )
        provider.turns.insert(0, [summary_provider_turn])
        session = Session()
        session.add_user_message("旧请求")
        session.add_assistant_message("旧回复")
        session.add_user_message("最新请求")
        context_manager = ContextManager(
            ContextManagementConfig(fallback_context_window_tokens=100, compact_trigger_ratio=0.5, recent_message_groups_to_keep=1),
            workdir=".",
        )
        runner = AgentRunner(
            provider,
            session,
            create_default_tool_registry(),
            ToolContext(Path.cwd()),
            context_manager=context_manager,
        )

        events = list(runner.stream(session.get_history()))

        context_events = [event for event in events if isinstance(event, AgentContextManagementEvent)]
        self.assertEqual(len(context_events), 1)
        self.assertTrue(context_events[0].automatic)
        self.assertTrue(context_events[0].result.compacted)

    def test_hook_tool_before_execute_blocks_original_tool_and_agent_continues(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            hooks_root = root / "hooks"
            hooks_root.mkdir()
            (hooks_root / "hooks.yaml").write_text(
                """
rules:
  - name: block-record
    event: tool_before_execute
    when:
      all:
        - field: tool.name
          exact: record
    intercept:
      reason: "blocked by hook"
    actions:
      - type: prompt_injection
        content: "record blocked"
""".strip(),
                encoding="utf-8",
            )
            tool = RecordingTool()
            registry = ToolRegistry()
            registry.register(tool)
            provider = ScriptedProvider(
                [
                    [ProviderTurnComplete(text="", tool_calls=[ToolCall(id="call-1", name="record", arguments={})])],
                    [ProviderTurnComplete(text="adjusted")],
                ]
            )
            session = Session()
            session.add_user_message("record")
            runner = AgentRunner(provider, session, registry, ToolContext(root), hook_engine=_hook_engine(root))

            result = runner.run(session.get_history())

        self.assertEqual(result, "adjusted")
        self.assertEqual(tool.calls, 0)
        tool_messages = [message.content for message in session.get_history() if message.role == "tool"]
        self.assertEqual(len(tool_messages), 1)
        self.assertIn("Hook 拦截", tool_messages[0])
        self.assertIn("blocked by hook", tool_messages[0])

    def test_hook_turn_start_prompt_injection_does_not_pollute_history(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            hooks_root = root / "hooks"
            hooks_root.mkdir()
            (hooks_root / "hooks.yaml").write_text(
                """
rules:
  - name: turn-reminder
    event: agent_turn_start
    actions:
      - type: prompt_injection
        content: "hook reminder"
""".strip(),
                encoding="utf-8",
            )
            provider = ScriptedProvider([[ProviderTurnComplete(text="done")]])
            session = Session()
            session.add_user_message("hello")
            runner = AgentRunner(
                provider,
                session,
                create_default_tool_registry(),
                ToolContext(root),
                prompt_assembler=PromptAssembler(default_prompt_modules()),
                runtime_context_factory=lambda: RuntimeContext(
                    workdir=root,
                    os_name="test-os",
                    current_date="2026-07-02",
                    provider_name="fake",
                    model="fake-model",
                ),
                hook_engine=_hook_engine(root),
            )

            runner.run(session.get_history())

        self.assertIn("hook reminder", provider.prompts[0].render_text())
        self.assertFalse(any("hook reminder" in message.content for message in session.get_history()))

    def test_hook_after_tool_execute_emits_diagnostic_without_modifying_result(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            hooks_root = root / "hooks"
            hooks_root.mkdir()
            (hooks_root / "hooks.yaml").write_text(
                """
rules:
  - name: after-tool
    event: tool_after_execute
    actions:
      - type: sub_agent
        description: "future work"
""".strip(),
                encoding="utf-8",
            )
            tool = RecordingTool()
            registry = ToolRegistry()
            registry.register(tool)
            provider = ScriptedProvider(
                [
                    [ProviderTurnComplete(text="", tool_calls=[ToolCall(id="call-1", name="record", arguments={})])],
                    [ProviderTurnComplete(text="done")],
                ]
            )
            session = Session()
            session.add_user_message("record")
            runner = AgentRunner(provider, session, registry, ToolContext(root), hook_engine=_hook_engine(root))

            events = list(runner.stream(session.get_history()))

        self.assertEqual(tool.calls, 1)
        diagnostics = [event.diagnostic.render() for event in events if isinstance(event, AgentHookDiagnosticEvent)]
        self.assertTrue(any("子 Agent" in item for item in diagnostics))
        tool_messages = [message.content for message in session.get_history() if message.role == "tool"]
        self.assertIn("recorded", tool_messages[0])


if __name__ == "__main__":
    unittest.main()
