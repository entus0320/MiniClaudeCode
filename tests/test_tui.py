import asyncio
import threading
import time
import unittest
from collections.abc import Mapping, Sequence
from typing import Any
from concurrent.futures import Future
from pathlib import Path
from tempfile import TemporaryDirectory

from textual.containers import VerticalScroll
from textual.widgets import OptionList, Static

from miniclaudecode.agent import ConfirmationResponse
from miniclaudecode.commands import Command, CommandRegistry, create_builtin_command_registry
from miniclaudecode.context import ContextManagementConfig, ContextManager
from miniclaudecode.hooks import HookEngine, HookLoader, HookRegistry
from miniclaudecode.observability import GenerationWatchConfig
from miniclaudecode.providers.base import BaseProvider, ProviderError, ProviderEvent, ProviderTextDelta, ProviderTurnComplete
from miniclaudecode.prompt_modules import default_prompt_modules
from miniclaudecode.prompting import PromptAssembler
from miniclaudecode.session import Message, Session
from miniclaudecode.skills import SkillLoader, SkillRegistry, SkillRuntime, register_skill_commands
from miniclaudecode.tools.base import Tool, ToolCall, ToolContext, ToolResult
from miniclaudecode.tools.defaults import create_default_tool_registry
from miniclaudecode.tools.registry import ToolRegistry
from miniclaudecode.tui import MiniClaudeCodeApp, _format_message, stream_to_text


class FakeProvider(BaseProvider):
    def __init__(self, parts: list[str], *, fail_after_first: bool = False) -> None:
        self.parts = parts
        self.fail_after_first = fail_after_first
        self.requests: list[list[Message]] = []
        self.prompts = []

    def stream_chat(self, messages, prompt=None):  # type: ignore[no-untyped-def]
        self.requests.append(list(messages))
        self.prompts.append(prompt)
        for index, part in enumerate(self.parts):
            yield part
            if self.fail_after_first and index == 0:
                raise ProviderError("fake failure")


class BlockingProvider(BaseProvider):
    def __init__(self, parts: list[str] | None = None) -> None:
        self.release = threading.Event()
        self.parts = parts or ["done"]
        self.requests: list[list[Message]] = []

    def stream_chat(self, messages, prompt=None):  # type: ignore[no-untyped-def]
        self.requests.append(list(messages))
        self.release.wait(timeout=5)
        for part in self.parts:
            yield part

    def stream_turn(self, messages: Sequence[Message], tools=None, prompt=None):  # type: ignore[no-untyped-def]
        self.requests.append(list(messages))
        self.release.wait(timeout=5)
        yield ProviderTextDelta("done")
        yield ProviderTurnComplete(text="done")


class ScriptedToolProvider(BaseProvider):
    def __init__(self, turns: list[list[ProviderEvent]], *, input_tokens: int | None = None) -> None:
        self.turns = turns
        self.input_tokens = input_tokens

    def count_tokens(self, messages, tools=None, prompt=None):  # type: ignore[no-untyped-def]
        return self.input_tokens

    def stream_chat(self, messages: Sequence[Message]):  # type: ignore[no-untyped-def]
        for event in self.stream_turn(messages):
            if isinstance(event, ProviderTurnComplete):
                yield event.text

    def stream_turn(self, messages: Sequence[Message], tools=None, prompt=None):  # type: ignore[no-untyped-def]
        yield from self.turns.pop(0)


class FakeMcpManager:
    def __init__(self) -> None:
        self.closed = False

    def close_all(self) -> None:
        self.closed = True


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


def _hook_engine(root: Path) -> HookEngine:
    registry = HookRegistry(HookLoader(project_root=root / "hooks"))
    registry.load_initial()
    return HookEngine(registry, tool_context=ToolContext(root))


def _valid_summary_text() -> str:
    return (
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


class TuiHelperTests(unittest.TestCase):
    def test_stream_to_text_collects_deltas(self) -> None:
        provider = FakeProvider(["po", "ng"])

        self.assertEqual(stream_to_text(provider, [Message(role="user", content="ping")]), "pong")

    def test_format_tool_message_escapes_markup_like_tags(self) -> None:
        rendered = _format_message("Tool", "result contains [/green] from model output")

        self.assertIn("\\[/green]", rendered)


class MiniClaudeCodeAppTests(unittest.IsolatedAsyncioTestCase):
    async def test_layout_contains_status_and_message_history(self) -> None:
        app = MiniClaudeCodeApp(
            FakeProvider([]),
            Session(),
            provider_name="fake",
            model="fake-model",
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            self.assertIn("fake", app.status_text)
            self.assertIn("fake-model", app.status_text)
            self.assertIn("MiniClaudeCode", app.welcome_text)
            self.assertIn("Welcome back!", app.welcome_text)
            self.assertIsInstance(app.query_one("#message-scroll"), VerticalScroll)
            history = app.query_one("#message-history", Static)
            self.assertTrue(history.has_class("welcome"))
            self.assertEqual(app.messages, [])

    async def test_plain_chat_generation_watchdog_reports_no_output_and_recovery(self) -> None:
        provider = BlockingProvider(["ok"])
        session = Session()
        app = MiniClaudeCodeApp(
            provider,
            session,
            provider_name="fake",
            model="fake-model",
            generation_watch_config=GenerationWatchConfig(idle_timeout_seconds=0.05, total_timeout_seconds=0.05),
        )

        async with app.run_test() as pilot:
            app.submit_user_message("slow")
            for _ in range(50):
                if any("没有模型输出" in content for _, content in app.messages):
                    break
                await pilot.pause(0.02)
            self.assertTrue(any("当前仅观测，不会自动中断" in content for _, content in app.messages))
            self.assertTrue("无模型输出" in app.status_text or "总耗时" in app.status_text)
            provider.release.set()
            await self._wait_for_status(app, "Ready")
            await pilot.pause()

        self.assertEqual(session.get_history()[-1].content, "ok")
        self.assertFalse(any("观测提示" in message.content for message in session.get_history()))

    async def test_agent_generation_watchdog_reports_round_in_system_message(self) -> None:
        provider = BlockingProvider(["done"])
        app = MiniClaudeCodeApp(
            provider,
            Session(),
            provider_name="fake",
            model="fake-model",
            tool_registry=create_default_tool_registry(),
            tool_context=ToolContext(Path.cwd()),
            generation_watch_config=GenerationWatchConfig(idle_timeout_seconds=0.05, total_timeout_seconds=0.05),
        )

        async with app.run_test() as pilot:
            app.submit_user_message("slow")
            for _ in range(50):
                if any("Agent 第 1 轮模型生成" in content for _, content in app.messages):
                    break
                await pilot.pause(0.02)
            self.assertTrue(any("Agent 第 1 轮模型生成" in content for _, content in app.messages))
            provider.release.set()
            await self._wait_for_status(app, "Ready")

    async def test_welcome_class_is_removed_after_first_message(self) -> None:
        app = MiniClaudeCodeApp(
            FakeProvider(["hi"]),
            Session(),
            provider_name="fake",
            model="fake-model",
        )

        async with app.run_test() as pilot:
            history = app.query_one("#message-history", Static)
            self.assertTrue(history.has_class("welcome"))
            app.submit_user_message("hello")
            await self._wait_for_status(app, "Ready")
            await pilot.pause()
            self.assertFalse(history.has_class("welcome"))

    async def test_long_message_history_can_scroll(self) -> None:
        app = MiniClaudeCodeApp(
            FakeProvider([]),
            Session(),
            provider_name="fake",
            model="fake-model",
        )

        async with app.run_test(size=(80, 12)) as pilot:
            app.messages = [("User", f"message {index}\nline two\nline three") for index in range(20)]
            app._refresh_messages()
            await pilot.pause()
            scroll = app.query_one("#message-scroll", VerticalScroll)
            scroll.scroll_end(animate=False)
            await pilot.pause()
            self.assertGreater(scroll.max_scroll_y, 0)
            self.assertGreater(scroll.scroll_y, 0)

    async def test_tool_calls_show_only_call_summary(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "note.txt").write_text("王勇大神", encoding="utf-8")
            provider = ScriptedToolProvider(
                [
                    [
                        ProviderTurnComplete(
                            text="",
                            tool_calls=[
                                ToolCall(id="call-1", name="read_file", arguments={"path": "note.txt"})
                            ],
                        )
                    ],
                    [ProviderTurnComplete(text="文件内容是王勇大神")],
                ]
            )
            app = MiniClaudeCodeApp(
                provider,
                Session(),
                provider_name="fake",
                model="fake-model",
                cwd=root,
                tool_registry=create_default_tool_registry(),
                tool_context=ToolContext(root),
            )

            async with app.run_test() as pilot:
                app.submit_user_message("read note")
                await self._wait_for_status(app, "Ready")
                await pilot.pause()

        tool_messages = [content for role, content in app.messages if role == "Tool"]
        system_messages = [content for role, content in app.messages if role == "System"]
        self.assertEqual(len(tool_messages), 1)
        self.assertIn("read_file", tool_messages[0])
        self.assertIn("note.txt", tool_messages[0])
        self.assertFalse(any("王勇大神" in content for content in tool_messages))
        self.assertFalse(any("工具 read_file 成功" in content for content in system_messages))
        self.assertEqual(app.messages[-1], ("Assistant", "文件内容是王勇大神"))

    async def test_tui_shows_offloaded_tool_result_notice(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "large.txt").write_text("preview-value-" + "x" * 1000, encoding="utf-8")
            context_manager = ContextManager(
                ContextManagementConfig(single_tool_result_token_limit=5, tool_result_preview_chars=30),
                workdir=root,
            )
            session = Session(tool_result_slimmer=context_manager.tool_result_slimmer)
            provider = ScriptedToolProvider(
                [
                    [ProviderTurnComplete(text="", tool_calls=[ToolCall(id="call-large", name="read_file", arguments={"path": "large.txt"})])],
                    [ProviderTurnComplete(text="done")],
                ]
            )
            app = MiniClaudeCodeApp(
                provider,
                session,
                provider_name="fake",
                model="fake-model",
                cwd=root,
                tool_registry=create_default_tool_registry(),
                tool_context=ToolContext(root),
                context_manager=context_manager,
            )

            async with app.run_test() as pilot:
                app.submit_user_message("read large")
                await self._wait_for_status(app, "Ready")
                await pilot.pause()

        system_messages = [content for role, content in app.messages if role == "System"]
        notice = "\n".join(system_messages)
        self.assertIn("工具结果已保存到 disk：read_file", notice)
        self.assertIn("tool-results", notice)
        self.assertIn("释放约", notice)
        self.assertIn("preview-value", notice)

    async def test_tui_shows_auto_compaction_notice_in_tool_mode(self) -> None:
        summary = _valid_summary_text()
        provider = ScriptedToolProvider(
            [
                [ProviderTurnComplete(text=summary)],
                [ProviderTurnComplete(text="done")],
            ],
            input_tokens=60,
        )
        session = Session()
        session.add_user_message("旧请求")
        session.add_assistant_message("旧回复")
        context_manager = ContextManager(
            ContextManagementConfig(fallback_context_window_tokens=100, compact_trigger_ratio=0.5, recent_message_groups_to_keep=1),
            workdir=".",
        )
        app = MiniClaudeCodeApp(
            provider,
            session,
            provider_name="fake",
            model="fake-model",
            tool_registry=create_default_tool_registry(),
            tool_context=ToolContext(Path.cwd()),
            context_manager=context_manager,
        )

        async with app.run_test() as pilot:
            app.submit_user_message("最新请求")
            await self._wait_for_status(app, "Ready")
            await pilot.pause()

        system_messages = [content for role, content in app.messages if role == "System"]
        self.assertTrue(any("已自动压缩" in content for content in system_messages))

    async def test_tui_shows_auto_compaction_notice_in_plain_chat_mode(self) -> None:
        provider = ScriptedToolProvider(
            [
                [ProviderTurnComplete(text=_valid_summary_text())],
                [ProviderTurnComplete(text="done")],
            ],
            input_tokens=60,
        )
        session = Session()
        session.add_user_message("旧请求")
        session.add_assistant_message("旧回复")
        context_manager = ContextManager(
            ContextManagementConfig(fallback_context_window_tokens=100, compact_trigger_ratio=0.5, recent_message_groups_to_keep=1),
            workdir=".",
        )
        app = MiniClaudeCodeApp(
            provider,
            session,
            provider_name="fake",
            model="fake-model",
            context_manager=context_manager,
        )

        async with app.run_test() as pilot:
            app.submit_user_message("最新请求")
            await self._wait_for_status(app, "Ready")
            await pilot.pause()

        system_messages = [content for role, content in app.messages if role == "System"]
        self.assertTrue(any("已自动压缩" in content for content in system_messages))
        self.assertEqual(app.messages[-1], ("Assistant", "done"))

    async def test_manual_compact_bad_model_summary_reports_failure_and_preserves_history(self) -> None:
        provider = ScriptedToolProvider([[ProviderTurnComplete(text="bad summary")], [ProviderTurnComplete(text="still bad")]])
        session = Session()
        session.add_user_message("旧请求")
        session.add_assistant_message("旧回复")
        session.add_user_message("最新请求")
        original_history = session.get_history()
        app = MiniClaudeCodeApp(
            provider,
            session,
            provider_name="fake",
            model="fake-model",
            context_manager=ContextManager(ContextManagementConfig(recent_message_groups_to_keep=1), workdir="."),
        )

        async with app.run_test() as pilot:
            input_box = app.query_one("#input-box")
            input_box.value = "/compact"
            await pilot.press("enter")
            await self._wait_for_status(app, "Ready")
            await pilot.pause()

        self.assertIn("上下文压缩失败", app.messages[-1][1])
        self.assertIn("原始历史已保留", app.messages[-1][1])
        self.assertEqual(session.get_history(), original_history)
        self.assertNotIn("本地兜底摘要", "\n".join(message.content for message in session.get_history()))

    async def test_multi_round_tool_calls_show_multiple_summaries(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "note.txt").write_text("王勇大神", encoding="utf-8")
            provider = ScriptedToolProvider(
                [
                    [ProviderTurnComplete(text="", tool_calls=[ToolCall(id="call-1", name="glob_files", arguments={"pattern": "**/note.txt"})])],
                    [ProviderTurnComplete(text="", tool_calls=[ToolCall(id="call-2", name="read_file", arguments={"path": "note.txt"})])],
                    [ProviderTurnComplete(text="文件内容是王勇大神")],
                ]
            )
            app = MiniClaudeCodeApp(
                provider,
                Session(),
                provider_name="fake",
                model="fake-model",
                cwd=root,
                tool_registry=create_default_tool_registry(),
                tool_context=ToolContext(root),
            )

            async with app.run_test() as pilot:
                app.submit_user_message("find and read note")
                await self._wait_for_status(app, "Ready")
                await pilot.pause()

        tool_messages = [content for role, content in app.messages if role == "Tool"]
        self.assertEqual(len(tool_messages), 2)
        self.assertIn("glob_files", tool_messages[0])
        self.assertIn("read_file", tool_messages[1])
        self.assertFalse(any("王勇大神" in content for content in tool_messages))
        self.assertEqual(app.messages[-1], ("Assistant", "文件内容是王勇大神"))

    async def test_max_rounds_final_synthesis_is_visible(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            provider = ScriptedToolProvider(
                [
                    [ProviderTurnComplete(text="", tool_calls=[ToolCall(id=f"call-{index}", name="glob_files", arguments={"pattern": "*.txt"})])]
                    for index in range(1, 21)
                ]
                + [[ProviderTurnComplete(text="当前信息不足以回答该问题。")]]
            )
            app = MiniClaudeCodeApp(
                provider,
                Session(),
                provider_name="fake",
                model="fake-model",
                cwd=root,
                tool_registry=create_default_tool_registry(),
                tool_context=ToolContext(root),
            )

            async with app.run_test() as pilot:
                app.submit_user_message("keep searching")
                await self._wait_for_status(app, "Ready")
                await pilot.pause()

        self.assertEqual(app.status, "Ready")
        self.assertEqual(len([content for role, content in app.messages if role == "Tool"]), 19)
        self.assertEqual(app.messages[-1][0], "Assistant")
        self.assertIn("最大轮数", app.messages[-1][1])
        self.assertIn("不足以回答", app.messages[-1][1])

    async def test_successful_stream_is_saved_to_session(self) -> None:
        provider = FakeProvider(["你", "好"])
        session = Session()
        app = MiniClaudeCodeApp(
            provider,
            session,
            provider_name="fake",
            model="fake-model",
        )

        async with app.run_test() as pilot:
            app.submit_user_message("hello")
            await self._wait_for_status(app, "Ready")
            await pilot.pause()

        self.assertEqual(
            session.get_history(),
            [
                Message(role="user", content="hello"),
                Message(role="assistant", content="你好"),
            ],
        )
        self.assertEqual(provider.requests[0], [Message(role="user", content="hello")])

    async def test_failed_stream_does_not_save_partial_assistant(self) -> None:
        provider = FakeProvider(["partial"], fail_after_first=True)
        session = Session()
        app = MiniClaudeCodeApp(
            provider,
            session,
            provider_name="fake",
            model="fake-model",
        )

        async with app.run_test() as pilot:
            app.submit_user_message("hello")
            await self._wait_for_status(app, "Ready")
            await pilot.pause()

        self.assertEqual(session.get_history(), [Message(role="user", content="hello")])
        self.assertIn("partial", app.messages[-2][1])
        self.assertIn("未保存", app.messages[-1][1])

    async def test_second_request_contains_previous_history(self) -> None:
        provider = FakeProvider(["hi"])
        session = Session()
        app = MiniClaudeCodeApp(
            provider,
            session,
            provider_name="fake",
            model="fake-model",
        )

        async with app.run_test() as pilot:
            app.submit_user_message("first")
            await self._wait_for_status(app, "Ready")
            app.submit_user_message("second")
            await self._wait_for_status(app, "Ready")
            await pilot.pause()

        self.assertEqual(
            provider.requests[1],
            [
                Message(role="user", content="first"),
                Message(role="assistant", content="hi"),
                Message(role="user", content="second"),
            ],
        )

    async def test_help_command_is_local_and_not_saved_to_session(self) -> None:
        session = Session()
        app = MiniClaudeCodeApp(
            FakeProvider([]),
            session,
            provider_name="fake",
            model="fake-model",
        )

        async with app.run_test() as pilot:
            input_box = app.query_one("#input-box")
            input_box.value = "/help"
            await pilot.press("enter")
            await pilot.pause()

        self.assertEqual(session.get_history(), [])
        self.assertIn("可用命令", app.messages[-1][1])
        self.assertIn("/compact", app.messages[-1][1])

    async def test_help_command_shows_single_command_detail(self) -> None:
        app = MiniClaudeCodeApp(
            FakeProvider([]),
            Session(),
            provider_name="fake",
            model="fake-model",
        )

        async with app.run_test() as pilot:
            input_box = app.query_one("#input-box")
            input_box.value = "/help resume"
            await pilot.press("enter")
            await pilot.pause()

        self.assertIn("/resume", app.messages[-1][1])
        self.assertIn("用法：/resume <session_id>", app.messages[-1][1])

    async def test_unknown_command_is_not_sent_to_provider(self) -> None:
        session = Session()
        provider = FakeProvider(["should not run"])
        app = MiniClaudeCodeApp(
            provider,
            session,
            provider_name="fake",
            model="fake-model",
        )

        async with app.run_test() as pilot:
            input_box = app.query_one("#input-box")
            input_box.value = "/unknown"
            await pilot.press("enter")
            await pilot.pause()

        self.assertEqual(session.get_history(), [])
        self.assertEqual(provider.requests, [])
        self.assertIn("未知命令", app.messages[-1][1])
        self.assertIn("/help", app.messages[-1][1])

    async def test_command_names_are_case_insensitive(self) -> None:
        app = MiniClaudeCodeApp(
            FakeProvider([]),
            Session(),
            provider_name="fake",
            model="fake-model",
        )

        async with app.run_test() as pilot:
            input_box = app.query_one("#input-box")
            input_box.value = "/SESSION"
            await pilot.press("enter")
            await pilot.pause()

        self.assertIn("会话存档未启用", app.messages[-1][1])

    async def test_resume_without_argument_shows_usage(self) -> None:
        app = MiniClaudeCodeApp(
            FakeProvider([]),
            Session(),
            provider_name="fake",
            model="fake-model",
        )

        async with app.run_test() as pilot:
            input_box = app.query_one("#input-box")
            input_box.value = "/resume"
            await pilot.press("enter")
            await pilot.pause()

        self.assertIn("用法：/resume <session_id>", app.messages[-1][1])

    async def test_notes_clear_only_shows_safety_message(self) -> None:
        app = MiniClaudeCodeApp(
            FakeProvider([]),
            Session(),
            provider_name="fake",
            model="fake-model",
        )

        async with app.run_test() as pilot:
            input_box = app.query_one("#input-box")
            input_box.value = "/notes-clear"
            await pilot.press("enter")
            await pilot.pause()

        self.assertIn("不会静默清空笔记", app.messages[-1][1])

    async def test_exit_command_returns_zero(self) -> None:
        app = MiniClaudeCodeApp(
            FakeProvider([]),
            Session(),
            provider_name="fake",
            model="fake-model",
        )

        async with app.run_test() as pilot:
            input_box = app.query_one("#input-box")
            input_box.value = "/exit"
            await pilot.press("enter")

        self.assertEqual(app.return_code, 0)

    async def test_startup_warnings_are_shown_and_mcp_manager_is_closed(self) -> None:
        manager = FakeMcpManager()
        app = MiniClaudeCodeApp(
            FakeProvider([]),
            Session(),
            provider_name="fake",
            model="fake-model",
            mcp_manager=manager,
            startup_warnings=("MCP warning",),
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            self.assertIn("MCP warning", app.messages[-1][1])

        self.assertTrue(manager.closed)

    async def test_confirmation_menu_is_shown_and_hidden(self) -> None:
        app = MiniClaudeCodeApp(
            FakeProvider([]),
            Session(),
            provider_name="fake",
            model="fake-model",
        )

        async with app.run_test() as pilot:
            menu = app.query_one("#confirmation-menu", OptionList)
            self.assertFalse(menu.display)
            future: Future[ConfirmationResponse] = Future()
            app._ask_tool_confirmation(future, "工具需要确认：write_file {}")
            await pilot.pause()

            self.assertTrue(menu.display)
            self.assertEqual(menu.option_count, 3)
            self.assertIn("1. Yes", app.messages[-1][1])
            self.assertTrue(app._resolve_pending_confirmation("allow_once"))
            await pilot.pause()

            self.assertEqual(future.result(), ConfirmationResponse.ALLOW_ONCE)
            self.assertIsNone(app._pending_confirmation)
            self.assertFalse(menu.display)

    async def test_confirmation_number_keys_resolve_pending_future(self) -> None:
        app = MiniClaudeCodeApp(
            FakeProvider([]),
            Session(),
            provider_name="fake",
            model="fake-model",
        )

        async with app.run_test() as pilot:
            allow_once: Future[ConfirmationResponse] = Future()
            app._ask_tool_confirmation(allow_once, "confirm")
            await pilot.pause()
            await pilot.press("1")
            self.assertTrue(allow_once.done())
            self.assertEqual(allow_once.result(), ConfirmationResponse.ALLOW_ONCE)

            allow_session: Future[ConfirmationResponse] = Future()
            app._ask_tool_confirmation(allow_session, "confirm")
            await pilot.pause()
            await pilot.press("2")
            self.assertTrue(allow_session.done())
            self.assertEqual(allow_session.result(), ConfirmationResponse.ALLOW_SESSION)

            deny: Future[ConfirmationResponse] = Future()
            app._ask_tool_confirmation(deny, "confirm")
            await pilot.pause()
            await pilot.press("3")
            self.assertTrue(deny.done())
            self.assertEqual(deny.result(), ConfirmationResponse.DENY)

    async def test_confirmation_number_input_with_enter_resolves_pending_future(self) -> None:
        app = MiniClaudeCodeApp(
            FakeProvider([]),
            Session(),
            provider_name="fake",
            model="fake-model",
        )

        async with app.run_test() as pilot:
            input_box = app.query_one("#input-box")
            future: Future[ConfirmationResponse] = Future()
            app._ask_tool_confirmation(future, "confirm")
            input_box.focus()
            await pilot.pause()
            input_box.value = "2"
            await pilot.press("enter")

            self.assertEqual(future.result(), ConfirmationResponse.ALLOW_SESSION)
            self.assertIsNone(app._pending_confirmation)

    async def test_confirmation_commands_resolve_pending_future(self) -> None:
        app = MiniClaudeCodeApp(
            FakeProvider([]),
            Session(),
            provider_name="fake",
            model="fake-model",
        )

        async with app.run_test() as pilot:
            input_box = app.query_one("#input-box")

            allow_once: Future[ConfirmationResponse] = Future()
            app._ask_tool_confirmation(allow_once, "confirm")
            input_box.focus()
            await pilot.pause()
            input_box.value = "/allow"
            await pilot.press("enter")
            self.assertEqual(allow_once.result(), ConfirmationResponse.ALLOW_ONCE)

            allow_session: Future[ConfirmationResponse] = Future()
            app._ask_tool_confirmation(allow_session, "confirm")
            input_box.focus()
            await pilot.pause()
            input_box.value = "/allow-session"
            await pilot.press("enter")
            self.assertEqual(allow_session.result(), ConfirmationResponse.ALLOW_SESSION)

            deny: Future[ConfirmationResponse] = Future()
            app._ask_tool_confirmation(deny, "confirm")
            input_box.focus()
            await pilot.pause()
            input_box.value = "/deny"
            await pilot.press("enter")
            self.assertEqual(deny.result(), ConfirmationResponse.DENY)

    async def test_pending_confirmation_blocks_other_commands(self) -> None:
        app = MiniClaudeCodeApp(
            FakeProvider([]),
            Session(),
            provider_name="fake",
            model="fake-model",
        )

        async with app.run_test() as pilot:
            input_box = app.query_one("#input-box")
            future: Future[ConfirmationResponse] = Future()
            app._ask_tool_confirmation(future, "confirm")
            input_box.focus()
            await pilot.pause()
            input_box.value = "/compact"
            await pilot.press("enter")
            await pilot.pause()

        self.assertFalse(future.done())
        self.assertIn("正在等待工具确认", app.messages[-1][1])

    async def test_command_completion_single_match_updates_input(self) -> None:
        app = MiniClaudeCodeApp(
            FakeProvider([]),
            Session(),
            provider_name="fake",
            model="fake-model",
        )

        async with app.run_test() as pilot:
            input_box = app.query_one("#input-box")
            input_box.value = "/comp"
            await pilot.press("tab")
            await pilot.pause()

        self.assertEqual(input_box.value, "/compact ")

    async def test_command_completion_multiple_matches_show_menu(self) -> None:
        app = MiniClaudeCodeApp(
            FakeProvider([]),
            Session(),
            provider_name="fake",
            model="fake-model",
        )

        async with app.run_test() as pilot:
            input_box = app.query_one("#input-box")
            input_box.value = "/ses"
            await pilot.press("tab")
            await pilot.pause()
            menu = app.query_one("#command-completion-menu", OptionList)
            self.assertTrue(menu.display)
            self.assertGreaterEqual(menu.option_count, 2)
            self.assertIn("mode: Command", app.status_text)

    async def test_command_completion_selection_updates_input(self) -> None:
        app = MiniClaudeCodeApp(
            FakeProvider([]),
            Session(),
            provider_name="fake",
            model="fake-model",
        )

        async with app.run_test() as pilot:
            input_box = app.query_one("#input-box")
            input_box.value = "/ses"
            await pilot.press("tab")
            await pilot.pause()
            app._accept_command_completion("/sessions")
            await pilot.pause()

        self.assertEqual(input_box.value, "/sessions ")

    async def test_hidden_confirmation_commands_are_not_completed(self) -> None:
        app = MiniClaudeCodeApp(
            FakeProvider([]),
            Session(),
            provider_name="fake",
            model="fake-model",
        )

        async with app.run_test() as pilot:
            input_box = app.query_one("#input-box")
            input_box.value = "/a"
            await pilot.press("tab")
            await pilot.pause()
            menu = app.query_one("#command-completion-menu", OptionList)

        self.assertFalse(menu.display)
        self.assertEqual(input_box.value, "/a")

    async def test_status_text_contains_mode_and_hints(self) -> None:
        app = MiniClaudeCodeApp(
            FakeProvider([]),
            Session(),
            provider_name="fake",
            model="fake-model",
        )

        async with app.run_test() as pilot:
            await pilot.pause()

        self.assertIn("mode: Chat", app.status_text)
        self.assertIn("/help /compact /interrupt", app.status_text)

    async def test_status_text_shows_confirm_mode_when_confirmation_is_pending(self) -> None:
        app = MiniClaudeCodeApp(
            FakeProvider([]),
            Session(),
            provider_name="fake",
            model="fake-model",
        )

        async with app.run_test() as pilot:
            future: Future[ConfirmationResponse] = Future()
            app._ask_tool_confirmation(future, "confirm")
            await pilot.pause()
            self.assertIn("mode: Confirm", app.status_text)

            self.assertTrue(app._resolve_pending_confirmation("deny"))
            await pilot.pause()

        self.assertIn("mode: Chat", app.status_text)

    async def test_command_handler_exception_is_shown_as_system_message(self) -> None:
        def broken_handler(ctx, invocation):  # type: ignore[no-untyped-def]
            raise RuntimeError("boom")

        registry = CommandRegistry()
        registry.register(Command(name="broken", handler=broken_handler))
        app = MiniClaudeCodeApp(
            FakeProvider([]),
            Session(),
            provider_name="fake",
            model="fake-model",
            command_registry=registry,
        )

        async with app.run_test() as pilot:
            input_box = app.query_one("#input-box")
            input_box.value = "/broken"
            await pilot.press("enter")
            await pilot.pause()

        self.assertIn("命令执行失败：/broken", app.messages[-1][1])
        self.assertIn("boom", app.messages[-1][1])

    async def test_confirmation_and_command_completion_selection_events_do_not_interfere(self) -> None:
        app = MiniClaudeCodeApp(
            FakeProvider([]),
            Session(),
            provider_name="fake",
            model="fake-model",
        )

        async with app.run_test() as pilot:
            input_box = app.query_one("#input-box")
            future: Future[ConfirmationResponse] = Future()
            app._ask_tool_confirmation(future, "confirm")
            await pilot.pause()

            app._show_command_completions(app.command_registry.completions("ses"))
            command_menu = app.query_one("#command-completion-menu", OptionList)
            command_menu.highlighted = 1
            command_menu.action_select()
            await pilot.pause()

            self.assertFalse(future.done())
            self.assertEqual(input_box.value, "/sessions ")

            confirmation_menu = app.query_one("#confirmation-menu", OptionList)
            confirmation_menu.highlighted = 2
            confirmation_menu.action_select()
            await pilot.pause()

        self.assertEqual(future.result(), ConfirmationResponse.DENY)

    async def test_skill_commands_show_activate_and_inject_prompt_without_history_pollution(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            skill_root = root / "skills"
            _write_test_skill(skill_root / "demo.md", body="DEMO SOP")
            tool_registry = create_default_tool_registry()
            command_registry = create_builtin_command_registry()
            skill_registry = SkillRegistry(
                SkillLoader(project_root=skill_root),
                tool_registry=tool_registry,
                command_registry=command_registry,
            )
            skill_registry.load_initial()
            skill_runtime = SkillRuntime(skill_registry)
            register_skill_commands(command_registry, skill_registry, skill_runtime)
            provider = FakeProvider(["ok"])
            session = Session()
            app = MiniClaudeCodeApp(
                provider,
                session,
                provider_name="fake",
                model="fake-model",
                command_registry=command_registry,
                skill_runtime=skill_runtime,
                prompt_assembler=PromptAssembler(default_prompt_modules()),
            )

            async with app.run_test() as pilot:
                input_box = app.query_one("#input-box")
                input_box.value = "/help"
                await pilot.press("enter")
                await pilot.pause()
                self.assertIn("/demo", app.messages[-1][1])

                input_box.value = "/demo"
                await pilot.press("enter")
                await pilot.pause()
                self.assertIn("已激活 Skill：/demo", app.messages[-1][1])

                app.submit_user_message("hello")
                await self._wait_for_status(app, "Ready")
                await pilot.pause()

        self.assertNotIn("DEMO SOP", "\n".join(message.content for message in session.get_history()))
        self.assertTrue(provider.prompts)
        self.assertIn("DEMO SOP", provider.prompts[-1].render_text())

    async def test_skill_management_commands_and_reload_hot_update(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            skill_path = root / "skills" / "demo.md"
            _write_test_skill(skill_path, body="OLD SOP")
            tool_registry = create_default_tool_registry()
            command_registry = create_builtin_command_registry()
            skill_registry = SkillRegistry(
                SkillLoader(project_root=root / "skills"),
                tool_registry=tool_registry,
                command_registry=command_registry,
            )
            skill_registry.load_initial()
            skill_runtime = SkillRuntime(skill_registry)
            register_skill_commands(command_registry, skill_registry, skill_runtime)
            app = MiniClaudeCodeApp(
                FakeProvider([]),
                Session(),
                provider_name="fake",
                model="fake-model",
                command_registry=command_registry,
                skill_runtime=skill_runtime,
            )

            async with app.run_test() as pilot:
                input_box = app.query_one("#input-box")
                input_box.value = "/skills"
                await pilot.press("enter")
                await pilot.pause()
                self.assertIn("/demo", app.messages[-1][1])

                input_box.value = "/skill demo"
                await pilot.press("enter")
                await pilot.pause()
                self.assertIn("模式：shared", app.messages[-1][1])

                _write_test_skill(skill_path, body="NEW SOP")
                input_box.value = "/demo"
                await pilot.press("enter")
                await pilot.pause()
                self.assertIn("NEW SOP", skill_runtime.active_instructions()[0].content)

                input_box.value = "/skills-reload"
                await pilot.press("enter")
                await pilot.pause()
                self.assertIn("Skill 重扫完成", app.messages[-1][1])

    async def test_confirmation_command_without_pending_is_not_user_message(self) -> None:
        session = Session()
        app = MiniClaudeCodeApp(
            FakeProvider([]),
            session,
            provider_name="fake",
            model="fake-model",
        )

        async with app.run_test() as pilot:
            input_box = app.query_one("#input-box")
            input_box.value = "/allow-session"
            await pilot.press("enter")
            await pilot.pause()

        self.assertEqual(session.get_history(), [])
        self.assertIn("没有等待确认", app.messages[-1][1])

    async def test_hook_startup_diagnostic_is_system_message_not_history(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            hooks_root = root / "hooks"
            hooks_root.mkdir()
            (hooks_root / "bad.yaml").write_text("rules: [", encoding="utf-8")
            registry = HookRegistry(HookLoader(project_root=hooks_root))
            registry.load_initial()
            app = MiniClaudeCodeApp(
                FakeProvider([]),
                Session(),
                provider_name="fake",
                model="fake-model",
                hook_engine=HookEngine(registry, tool_context=ToolContext(root)),
                startup_warnings=tuple(diagnostic.render() for diagnostic in registry.diagnostics),
            )

            async with app.run_test() as pilot:
                await pilot.pause()

            self.assertTrue(any("Hook" in content and "YAML" in content for _, content in app.messages))
            self.assertEqual(app.session.get_history(), [])

    async def test_hook_message_before_send_injects_prompt_without_history_pollution(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            hooks_root = root / "hooks"
            hooks_root.mkdir()
            (hooks_root / "hooks.yaml").write_text(
                """
rules:
  - name: inject
    event: message_before_send
    actions:
      - type: prompt_injection
        content: "HOOK PROMPT ${message.content}"
""".strip(),
                encoding="utf-8",
            )
            provider = FakeProvider(["ok"])
            session = Session()
            app = MiniClaudeCodeApp(
                provider,
                session,
                provider_name="fake",
                model="fake-model",
                prompt_assembler=PromptAssembler(default_prompt_modules()),
                hook_engine=_hook_engine(root),
            )

            async with app.run_test() as pilot:
                app.submit_user_message("hello")
                await self._wait_for_status(app, "Ready")
                await pilot.pause()

            self.assertTrue(provider.prompts)
            self.assertIn("HOOK PROMPT hello", provider.prompts[-1].render_text())
            self.assertFalse(any("HOOK PROMPT" in message.content for message in session.get_history()))

    async def test_hook_tool_intercept_is_visible_and_original_tool_not_called(self) -> None:
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
      reason: "blocked in tui"
    actions:
      - type: prompt_injection
        content: "blocked"
""".strip(),
                encoding="utf-8",
            )
            tool = RecordingTool()
            registry = ToolRegistry()
            registry.register(tool)
            provider = ScriptedToolProvider(
                [
                    [ProviderTurnComplete(text="", tool_calls=[ToolCall(id="call-1", name="record", arguments={})])],
                    [ProviderTurnComplete(text="done")],
                ]
            )
            app = MiniClaudeCodeApp(
                provider,
                Session(),
                provider_name="fake",
                model="fake-model",
                tool_registry=registry,
                tool_context=ToolContext(root),
                hook_engine=_hook_engine(root),
            )

            async with app.run_test() as pilot:
                app.submit_user_message("record")
                await self._wait_for_status(app, "Ready")
                await pilot.pause()

            self.assertEqual(tool.calls, 0)
            self.assertTrue(any("调用工具" in content and "record" in content for _, content in app.messages))
            tool_messages = [message.content for message in app.session.get_history() if message.role == "tool"]
            self.assertEqual(len(tool_messages), 1)
            self.assertIn("blocked in tui", tool_messages[0])

    async def _wait_for_status(self, app: MiniClaudeCodeApp, status: str) -> None:
        for _ in range(500):
            if app.status == status:
                return
            await asyncio.sleep(0.01)
        self.fail(f"Timed out waiting for status {status!r}; current status: {app.status!r}")


def _write_test_skill(path: Path, *, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        "name: demo\n"
        "description: demo skill\n"
        "tools:\n"
        "  - read_file\n"
        "mode: shared\n"
        "context: full-summary\n"
        "---\n\n"
        f"{body}",
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
