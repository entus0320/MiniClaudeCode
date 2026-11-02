import unittest
from collections.abc import Iterator, Sequence

from miniclaudecode.context import ContextManagementConfig, ContextManager, extract_formal_summary, group_messages, summary_instruction
from miniclaudecode.providers.base import BaseProvider, ProviderEvent, ProviderTurnComplete
from miniclaudecode.session import Message, Session
from miniclaudecode.tools.base import Tool, ToolCall


SUMMARY = """<analysis_draft>
草稿，不应进入 session。
</analysis_draft>
<formal_summary>
## 主要请求
继续开发。
## 关键概念
上下文管理。
## 文件代码
src/miniclaudecode/context.py。
## 错误修复
无。
## 解决过程
已压缩旧历史。
## 用户原话
“继续开发”。
## 待办
继续测试。
## 当前工作
实现压缩。
## 下一步
运行验证。
</formal_summary>"""


class SummaryProvider(BaseProvider):
    def __init__(
        self,
        text: str = SUMMARY,
        *,
        tool_calls=None,  # type: ignore[no-untyped-def]
        fail: bool = False,
        input_tokens: int | None = None,
        context_window_tokens: int | None = None,
    ) -> None:
        self.text = text
        self.tool_calls = [] if tool_calls is None else tool_calls
        self.fail = fail
        self.input_tokens = input_tokens
        self._context_window_tokens = context_window_tokens
        self.calls = 0
        self.texts = [text]

    @property
    def context_window_tokens(self) -> int | None:
        return self._context_window_tokens

    def count_tokens(self, messages: Sequence[Message], tools: Sequence[Tool] | None = None, prompt=None) -> int | None:  # type: ignore[no-untyped-def]
        return self.input_tokens

    def stream_chat(self, messages: Sequence[Message], prompt=None) -> Iterator[str]:  # type: ignore[no-untyped-def]
        return iter(())

    def stream_turn(self, messages: Sequence[Message], tools: Sequence[Tool] | None = None, prompt=None) -> Iterator[ProviderEvent]:  # type: ignore[no-untyped-def]
        self.calls += 1
        self.last_messages = list(messages)
        self.last_tools = tools
        if self.fail:
            raise RuntimeError("summary failed")
        index = min(self.calls - 1, len(self.texts) - 1)
        yield ProviderTurnComplete(text=self.texts[index], tool_calls=list(self.tool_calls))


class ContextCompactionTests(unittest.TestCase):
    def test_group_messages_keeps_tool_calls_with_results(self) -> None:
        messages = [
            Message(role="user", content="read"),
            Message(role="assistant", content="", tool_calls=[ToolCall(id="call-1", name="read_file")]),
            Message(role="tool", content="result", tool_call_id="call-1", tool_name="read_file"),
            Message(role="assistant", content="done"),
        ]

        groups = group_messages(messages)

        self.assertEqual(len(groups), 3)
        self.assertEqual(groups[1].messages, (messages[1], messages[2]))
        self.assertTrue(groups[1].compactable)

    def test_compact_replaces_old_history_with_summary_boundary_and_recent_messages(self) -> None:
        with self.subTest("manual compaction"):
            session = Session()
            session.add_user_message("旧请求")
            session.add_assistant_message("旧回复")
            latest = session.add_user_message("最新请求原文")
            provider = SummaryProvider()
            manager = ContextManager(
                ContextManagementConfig(recent_message_groups_to_keep=1),
                workdir=".",
            )

            result = manager.manual_compact(session=session, provider=provider)

            history = session.get_history()
            self.assertTrue(result.compacted)
            self.assertEqual(provider.last_tools, [])
            self.assertEqual(history[0].synthetic_kind, "context_summary")
            self.assertEqual(history[1].synthetic_kind, "context_boundary")
            self.assertNotIn("analysis_draft", history[0].content)
            self.assertIn("## 主要请求", history[0].content)
            self.assertIn("必须重新读取", history[1].content)
            self.assertIn("不要根据摘要脑补", history[1].content)
            self.assertEqual(history[-1], latest)

    def test_auto_compact_uses_fallback_window_when_provider_window_is_unknown(self) -> None:
        session = Session()
        session.add_user_message("旧请求")
        session.add_assistant_message("旧回复")
        session.add_user_message("最新请求")
        provider = SummaryProvider(input_tokens=60, context_window_tokens=None)
        manager = ContextManager(
            ContextManagementConfig(
                fallback_context_window_tokens=100,
                compact_trigger_ratio=0.5,
                recent_message_groups_to_keep=1,
            ),
            workdir=".",
        )

        result = manager.prepare_before_request(session=session, provider=provider, tools=[], prompt=None)

        self.assertTrue(result.compacted)
        self.assertEqual(result.context_window_tokens, 100)
        self.assertEqual(provider.calls, 1)

    def test_auto_compact_under_budget_uses_fallback_window(self) -> None:
        session = Session()
        session.add_user_message("短历史")
        provider = SummaryProvider(input_tokens=10, context_window_tokens=None)
        manager = ContextManager(
            ContextManagementConfig(fallback_context_window_tokens=100, compact_trigger_ratio=0.8),
            workdir=".",
        )

        result = manager.prepare_before_request(session=session, provider=provider, tools=[], prompt=None)

        self.assertFalse(result.compacted)
        self.assertEqual(result.reason, "under_budget")
        self.assertEqual(result.context_window_tokens, 100)
        self.assertEqual(provider.calls, 0)

    def test_manual_compact_can_force_short_history_below_default_keep_count(self) -> None:
        session = Session()
        session.add_user_message("旧请求")
        session.add_assistant_message("旧回复")
        latest = session.add_user_message("最新请求")
        provider = SummaryProvider()
        manager = ContextManager(ContextManagementConfig(), workdir=".")

        result = manager.manual_compact(session=session, provider=provider)

        history = session.get_history()
        self.assertTrue(result.compacted)
        self.assertEqual(history[0].synthetic_kind, "context_summary")
        self.assertEqual(history[1].synthetic_kind, "context_boundary")
        self.assertEqual(history[-1], latest)
        self.assertEqual(result.retained_message_count, 1)

    def test_manual_compact_still_requires_at_least_two_groups(self) -> None:
        session = Session()
        session.add_user_message("唯一请求")
        manager = ContextManager(ContextManagementConfig(), workdir=".")

        result = manager.manual_compact(session=session, provider=SummaryProvider())

        self.assertFalse(result.compacted)
        self.assertEqual(result.reason, "not_enough_history")

    def test_summary_request_carries_instruction_and_history_in_user_message(self) -> None:
        session = Session()
        session.add_user_message("修复登录 bug")
        session.add_assistant_message("我已经定位到 auth.py")
        session.add_user_message("最新请求")
        provider = SummaryProvider()
        manager = ContextManager(ContextManagementConfig(recent_message_groups_to_keep=1), workdir=".")

        result = manager.manual_compact(session=session, provider=provider)

        self.assertTrue(result.compacted)
        sent = provider.last_messages
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0].role, "user")
        self.assertIn("formal_summary", sent[0].content)
        self.assertIn("修复登录 bug", sent[0].content)
        self.assertIn("我已经定位到 auth.py", sent[0].content)

    def test_missing_required_heading_fails_and_preserves_history(self) -> None:
        text = "<formal_summary>\n## 主要请求\n只有一项。\n</formal_summary>"
        session = Session()
        session.add_user_message("a")
        session.add_assistant_message("b")
        session.add_user_message("c")
        original_history = session.get_history()
        provider = SummaryProvider(text)
        manager = ContextManager(ContextManagementConfig(recent_message_groups_to_keep=1), workdir=".")

        result = manager.manual_compact(session=session, provider=provider)

        self.assertFalse(result.compacted)
        self.assertEqual(result.reason, "summary_failed")
        self.assertIn("模型未返回可用的结构化摘要", result.error or "")
        self.assertEqual(session.get_history(), original_history)
        self.assertEqual(provider.calls, 2)
        self.assertEqual(manager.state.consecutive_failures, 1)

    def test_tool_calls_returned_by_summary_are_rejected_and_fuse_after_retries(self) -> None:
        session = Session()
        for index in range(4):
            session.add_user_message(f"u{index}")
        provider = SummaryProvider(tool_calls=[ToolCall(id="call-tool", name="read_file")])
        manager = ContextManager(
            ContextManagementConfig(recent_message_groups_to_keep=1, max_auto_compact_failures=2),
            workdir=".",
        )

        first = manager.manual_compact(session=session, provider=provider)
        second = manager.manual_compact(session=session, provider=provider)

        self.assertFalse(first.compacted)
        self.assertFalse(second.compacted)
        self.assertFalse(manager.state.auto_enabled)
        self.assertIn("工具调用", manager.state.last_error or "")

    def test_summary_instruction_outputs_formal_summary_only(self) -> None:
        text = summary_instruction().content

        self.assertNotIn("analysis_draft", text)
        self.assertIn("<formal_summary>", text)
        self.assertIn("最后一行必须是 </formal_summary>", text)

    def test_extract_formal_summary_rejects_heading_only_output(self) -> None:
        text = """<analysis_draft>
草稿。
</analysis_draft>
## 主要请求
继续开发。
## 关键概念
上下文管理。
## 文件代码
src/miniclaudecode/context.py。
## 错误修复
无。
## 解决过程
已压缩旧历史。
## 用户原话
“继续开发”。
## 待办
继续测试。
## 当前工作
实现压缩。
## 下一步
运行验证。"""

        with self.assertRaisesRegex(ValueError, "formal_summary"):
            extract_formal_summary(text)

    def test_extract_formal_summary_accepts_common_format_variants(self) -> None:
        text = """```xml
<FORMAL_SUMMARY>
###主要请求
继续开发。
###关键概念
上下文管理。
###文件代码
src/miniclaudecode/context.py。
###错误修复
无。
###解决过程
已压缩旧历史。
###用户原话
“继续开发”。
###待办
继续测试。
###当前工作
实现压缩。
###下一步
运行验证。
</FORMAL_SUMMARY>
```"""

        formal = extract_formal_summary(text)

        self.assertIn("## 主要请求", formal)
        self.assertIn("## 下一步", formal)

    def test_extract_formal_summary_rejects_missing_closing_tag(self) -> None:
        text = """<formal_summary>
##主要请求
继续开发。
##关键概念
上下文管理。
##文件代码
src/miniclaudecode/context.py。
##错误修复
无。
##解决过程
已压缩旧历史。
##用户原话
“继续开发”。
##待办
继续测试。
##当前工作
实现压缩。
##下一步
运行验证。"""

        with self.assertRaisesRegex(ValueError, "formal_summary"):
            extract_formal_summary(text)

    def test_summarize_retries_once_on_missing_formal_summary(self) -> None:
        session = Session()
        session.add_user_message("旧请求")
        session.add_assistant_message("旧回复")
        session.add_user_message("最新请求")
        provider = SummaryProvider("bad summary")
        provider.texts = ["bad summary", SUMMARY]
        manager = ContextManager(ContextManagementConfig(recent_message_groups_to_keep=1), workdir=".")

        result = manager.manual_compact(session=session, provider=provider)

        self.assertTrue(result.compacted)
        self.assertEqual(provider.calls, 2)

    def test_summarize_fails_when_retry_also_lacks_formal_summary(self) -> None:
        session = Session()
        session.add_user_message("旧请求")
        session.add_assistant_message("旧回复")
        session.add_user_message("最新请求")
        original_history = session.get_history()
        provider = SummaryProvider("bad summary")
        provider.texts = ["bad summary", "still bad"]
        manager = ContextManager(ContextManagementConfig(recent_message_groups_to_keep=1), workdir=".")

        result = manager.manual_compact(session=session, provider=provider)

        self.assertFalse(result.compacted)
        self.assertEqual(result.reason, "summary_failed")
        self.assertEqual(provider.calls, 2)
        self.assertEqual(session.get_history(), original_history)

    def test_compact_success_reports_estimated_token_reduction(self) -> None:
        session = Session()
        session.add_user_message("旧请求" * 200)
        session.add_assistant_message("旧回复" * 200)
        session.add_user_message("最新请求")
        manager = ContextManager(ContextManagementConfig(recent_message_groups_to_keep=1), workdir=".")

        result = manager.manual_compact(session=session, provider=SummaryProvider())

        self.assertTrue(result.compacted)
        self.assertIsNotNone(result.before_estimated_tokens)
        self.assertIsNotNone(result.after_estimated_tokens)
        self.assertIsNotNone(result.released_estimated_tokens)
        self.assertGreater(result.before_estimated_tokens or 0, result.after_estimated_tokens or 0)

    def test_extract_formal_summary_requires_formal_block_or_all_headings(self) -> None:
        with self.assertRaisesRegex(ValueError, "formal_summary"):
            extract_formal_summary("no formal block")


if __name__ == "__main__":
    unittest.main()
