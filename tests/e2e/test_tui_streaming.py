import asyncio
import unittest

from miniclaudecode.providers.base import BaseProvider
from miniclaudecode.session import Message, Session
from miniclaudecode.tui import MiniClaudeCodeApp


class RecordingProvider(BaseProvider):
    def __init__(self, responses: list[list[str]]) -> None:
        self.responses = responses
        self.requests: list[list[Message]] = []

    def stream_chat(self, messages):  # type: ignore[no-untyped-def]
        self.requests.append(list(messages))
        parts = self.responses.pop(0)
        for part in parts:
            yield part


class TuiStreamingE2ETests(unittest.IsolatedAsyncioTestCase):
    async def test_fake_anthropic_style_multi_turn_history(self) -> None:
        provider = RecordingProvider([
            ["你好", "小明"],
            ["你叫", "小明"],
        ])
        session = Session()
        app = MiniClaudeCodeApp(
            provider,
            session,
            provider_name="anthropic",
            model="claude-opus-4-8",
        )

        async with app.run_test() as pilot:
            app.submit_user_message("我叫小明")
            await self._wait_for_status(app, "Ready")
            app.submit_user_message("我叫什么？")
            await self._wait_for_status(app, "Ready")
            await pilot.pause()

        self.assertEqual(
            provider.requests[1],
            [
                Message(role="user", content="我叫小明"),
                Message(role="assistant", content="你好小明"),
                Message(role="user", content="我叫什么？"),
            ],
        )
        self.assertEqual(session.get_history()[-1], Message(role="assistant", content="你叫小明"))

    async def test_fake_openai_compatible_streams_pong(self) -> None:
        provider = RecordingProvider([["po", "ng"]])
        session = Session()
        app = MiniClaudeCodeApp(
            provider,
            session,
            provider_name="openai-compatible",
            model="compatible-model",
        )

        async with app.run_test() as pilot:
            app.submit_user_message("只回复 pong")
            await self._wait_for_status(app, "Ready")
            await pilot.pause()

        self.assertEqual(session.get_history()[-1], Message(role="assistant", content="pong"))
        self.assertIn(("Assistant", "pong"), app.messages)

    async def _wait_for_status(self, app: MiniClaudeCodeApp, status: str) -> None:
        for _ in range(100):
            if app.status == status:
                return
            await asyncio.sleep(0.01)
        self.fail(f"Timed out waiting for status {status!r}; current status: {app.status!r}")


if __name__ == "__main__":
    unittest.main()
