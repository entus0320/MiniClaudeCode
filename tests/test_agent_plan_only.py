import unittest
from collections.abc import Iterator, Sequence
from pathlib import Path
from tempfile import TemporaryDirectory

from miniclaudecode.agent import AgentCallbacks, AgentOptions, AgentRunner
from miniclaudecode.prompt_modules import default_prompt_modules
from miniclaudecode.prompting import PromptAssembler, RuntimeContext
from miniclaudecode.providers.base import BaseProvider, ProviderEvent, ProviderTextDelta, ProviderTurnComplete
from miniclaudecode.session import Message, Session
from miniclaudecode.tools.base import ToolCall, ToolContext
from miniclaudecode.tools.defaults import create_default_tool_registry


class ScriptedProvider(BaseProvider):
    def __init__(self, turns: list[list[ProviderEvent]]) -> None:
        self.turns = turns
        self.prompts = []

    def stream_chat(self, messages: Sequence[Message], prompt=None) -> Iterator[str]:  # type: ignore[no-untyped-def]
        stream = self.stream_turn(messages, prompt=prompt) if prompt is not None else self.stream_turn(messages)
        for event in stream:
            if isinstance(event, ProviderTextDelta):
                yield event.text

    def stream_turn(self, messages: Sequence[Message], tools=None, prompt=None):  # type: ignore[no-untyped-def]
        self.prompts.append(prompt)
        yield from self.turns.pop(0)


class AgentPlanOnlyTests(unittest.TestCase):
    def test_plan_only_allows_read_file(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "note.txt").write_text("readable", encoding="utf-8")
            session = Session()
            session.add_user_message("read")
            provider = ScriptedProvider(
                [
                    [ProviderTurnComplete(text="", tool_calls=[ToolCall(id="call-1", name="read_file", arguments={"path": "note.txt"})])],
                    [ProviderTurnComplete(text="done")],
                ]
            )
            runner = AgentRunner(
                provider,
                session,
                create_default_tool_registry(),
                ToolContext(root),
                options=AgentOptions(plan_only=True),
            )

            runner.run(session.get_history())

        tool_messages = [message.content for message in session.get_history() if message.role == "tool"]
        self.assertTrue(any("readable" in content for content in tool_messages))

    def test_plan_only_adds_dynamic_instruction_without_polluting_session(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            session = Session()
            session.add_user_message("write")
            provider = ScriptedProvider(
                [
                    [ProviderTurnComplete(text="", tool_calls=[ToolCall(id="call-1", name="write_file", arguments={"path": "new.txt", "content": "x"})])],
                    [ProviderTurnComplete(text="blocked")],
                ]
            )
            runner = AgentRunner(
                provider,
                session,
                create_default_tool_registry(),
                ToolContext(root),
                options=AgentOptions(plan_only=True),
                prompt_assembler=PromptAssembler(default_prompt_modules()),
                runtime_context_factory=lambda: RuntimeContext(
                    workdir=root,
                    os_name="test-os",
                    current_date="2026-06-14",
                    provider_name="fake",
                    model="fake-model",
                ),
            )

            runner.run(session.get_history())

            self.assertIsNotNone(provider.prompts[0])
            prompt_text = provider.prompts[0].render_text()
            self.assertIn("plan-only", prompt_text)
            self.assertIn("write_file", prompt_text)
            self.assertFalse(any("<system-reminder>" in message.content for message in session.get_history()))
            self.assertFalse((root / "new.txt").exists())

    def test_plan_only_blocks_write_file_before_confirmation(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            session = Session()
            session.add_user_message("write")
            provider = ScriptedProvider(
                [
                    [ProviderTurnComplete(text="", tool_calls=[ToolCall(id="call-1", name="write_file", arguments={"path": "new.txt", "content": "x"})])],
                    [ProviderTurnComplete(text="blocked")],
                ]
            )
            confirm_calls: list[str] = []
            callbacks = AgentCallbacks(confirm_tool=lambda call, tool: confirm_calls.append(tool.name) is None)
            runner = AgentRunner(
                provider,
                session,
                create_default_tool_registry(),
                ToolContext(root),
                callbacks,
                AgentOptions(plan_only=True),
            )

            runner.run(session.get_history())

            self.assertFalse((root / "new.txt").exists())
            self.assertEqual(confirm_calls, [])
            tool_content = next(message.content for message in session.get_history() if message.role == "tool")
            self.assertIn("plan-only", tool_content)
            self.assertIn("blocked_by", tool_content)

    def test_plan_only_blocks_edit_file(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "note.txt"
            target.write_text("old", encoding="utf-8")
            session = Session()
            session.add_user_message("edit")
            provider = ScriptedProvider(
                [
                    [ProviderTurnComplete(text="", tool_calls=[ToolCall(id="call-1", name="edit_file", arguments={"path": "note.txt", "old_text": "old", "new_text": "new"})])],
                    [ProviderTurnComplete(text="blocked")],
                ]
            )
            runner = AgentRunner(
                provider,
                session,
                create_default_tool_registry(),
                ToolContext(root),
                options=AgentOptions(plan_only=True),
            )

            runner.run(session.get_history())

            self.assertEqual(target.read_text(encoding="utf-8"), "old")
            tool_content = next(message.content for message in session.get_history() if message.role == "tool")
            self.assertIn("plan-only", tool_content)

    def test_plan_only_blocks_run_command(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            session = Session()
            session.add_user_message("run")
            provider = ScriptedProvider(
                [
                    [ProviderTurnComplete(text="", tool_calls=[ToolCall(id="call-1", name="run_command", arguments={"command": "printf x > command.txt"})])],
                    [ProviderTurnComplete(text="blocked")],
                ]
            )
            runner = AgentRunner(
                provider,
                session,
                create_default_tool_registry(),
                ToolContext(root),
                options=AgentOptions(plan_only=True),
            )

            runner.run(session.get_history())

            self.assertFalse((root / "command.txt").exists())
            tool_content = next(message.content for message in session.get_history() if message.role == "tool")
            self.assertIn("plan-only", tool_content)


if __name__ == "__main__":
    unittest.main()
