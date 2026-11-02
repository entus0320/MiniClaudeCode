import unittest
from collections.abc import Iterator, Sequence
from pathlib import Path
from tempfile import TemporaryDirectory

from miniclaudecode.agent import AgentCallbacks, AgentOptions, AgentRunner
from miniclaudecode.providers.base import BaseProvider, ProviderEvent, ProviderTextDelta, ProviderTurnComplete
from miniclaudecode.session import Message, Session
from miniclaudecode.tools.base import ToolCall, ToolContext
from miniclaudecode.tools.defaults import create_default_tool_registry


class ScriptedProvider(BaseProvider):
    def __init__(self, turns: list[list[ProviderEvent]]) -> None:
        self.turns = turns
        self.requests: list[list[Message]] = []

    def stream_chat(self, messages: Sequence[Message]) -> Iterator[str]:
        for event in self.stream_turn(messages):
            if isinstance(event, ProviderTextDelta):
                yield event.text

    def stream_turn(self, messages: Sequence[Message], tools=None):  # type: ignore[no-untyped-def]
        self.requests.append(list(messages))
        yield from self.turns.pop(0)


class ToolFlowE2ETests(unittest.TestCase):
    def test_glob_then_read_file_then_final_summary(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("MiniClaudeCode is a terminal agent.", encoding="utf-8")
            provider = ScriptedProvider([
                [ProviderTurnComplete(text="", tool_calls=[ToolCall(id="call-1", name="glob_files", arguments={"pattern": "README.md"})])],
                [ProviderTurnComplete(text="", tool_calls=[ToolCall(id="call-2", name="read_file", arguments={"path": "README.md"})])],
                [ProviderTextDelta("README says MiniClaudeCode is a terminal agent."), ProviderTurnComplete(text="README says MiniClaudeCode is a terminal agent.")],
            ])
            session = Session()
            session.add_user_message("find and read README")
            runner = AgentRunner(provider, session, create_default_tool_registry(), ToolContext(root))

            final = runner.run(session.get_history())

        self.assertIn("terminal agent", final)
        self.assertEqual(len(provider.requests), 3)
        self.assertTrue(any(message.role == "tool" and "README.md" in message.content for message in provider.requests[1]))
        self.assertTrue(any(message.role == "tool" and "MiniClaudeCode" in message.content for message in provider.requests[2]))

    def test_grep_known_symbol_returns_path_before_summary(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "src.py"
            source.write_text("class MiniClaudeCodeApp:\n    pass\n", encoding="utf-8")
            provider = ScriptedProvider([
                [ProviderTurnComplete(text="", tool_calls=[ToolCall(id="call-1", name="grep_content", arguments={"pattern": "MiniClaudeCodeApp", "glob": "*.py"})])],
                [ProviderTurnComplete(text="found src.py")],
            ])
            session = Session()
            session.add_user_message("find symbol")
            runner = AgentRunner(provider, session, create_default_tool_registry(), ToolContext(root))

            runner.run(session.get_history())

        self.assertTrue(any("src.py:1" in message.content for message in session.get_history() if message.role == "tool"))

    def test_edit_multiple_matches_summarizes_failure(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "note.txt").write_text("x x", encoding="utf-8")
            provider = ScriptedProvider([
                [ProviderTurnComplete(text="", tool_calls=[ToolCall(id="call-1", name="edit_file", arguments={"path": "note.txt", "old_text": "x", "new_text": "y"})])],
                [ProviderTurnComplete(text="cannot uniquely match")],
            ])
            session = Session()
            session.add_user_message("edit")
            callbacks = AgentCallbacks(confirm_tool=lambda call, tool: True)
            runner = AgentRunner(provider, session, create_default_tool_registry(), ToolContext(root), callbacks)

            final = runner.run(session.get_history())

        self.assertIn("uniquely", final)
        self.assertTrue(any("匹配多次" in message.content for message in session.get_history() if message.role == "tool"))

    def test_plan_only_blocks_write_file(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            provider = ScriptedProvider([
                [ProviderTurnComplete(text="", tool_calls=[ToolCall(id="call-1", name="write_file", arguments={"path": "new.txt", "content": "x"})])],
                [ProviderTurnComplete(text="blocked by plan-only")],
            ])
            session = Session()
            session.add_user_message("plan write")
            runner = AgentRunner(
                provider,
                session,
                create_default_tool_registry(),
                ToolContext(root),
                options=AgentOptions(plan_only=True),
            )

            final = runner.run(session.get_history())

        self.assertIn("plan-only", final)
        self.assertFalse((root / "new.txt").exists())
        self.assertTrue(any("plan-only" in message.content for message in session.get_history() if message.role == "tool"))


if __name__ == "__main__":
    unittest.main()
