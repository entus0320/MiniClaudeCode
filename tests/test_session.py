import tempfile
import unittest
from pathlib import Path

from miniclaudecode.session import Message, Session


class MessageTests(unittest.TestCase):
    def test_message_rejects_unknown_role(self) -> None:
        with self.assertRaisesRegex(ValueError, "Message role"):
            Message(role="system", content="hello")

    def test_message_rejects_non_string_content(self) -> None:
        with self.assertRaisesRegex(TypeError, "content"):
            Message(role="user", content=object())  # type: ignore[arg-type]


class SessionTests(unittest.TestCase):
    def test_session_starts_empty(self) -> None:
        session = Session()

        self.assertEqual(session.get_history(), [])

    def test_session_preserves_multi_turn_history_order(self) -> None:
        session = Session()

        first_user = session.add_user_message("我叫小明")
        first_assistant = session.add_assistant_message("你好，小明")
        second_user = session.add_user_message("我叫什么？")

        self.assertEqual(session.get_history(), [first_user, first_assistant, second_user])
        self.assertEqual(
            [message.role for message in session.get_history()],
            ["user", "assistant", "user"],
        )

    def test_get_history_returns_snapshot(self) -> None:
        session = Session()
        session.add_user_message("hello")

        snapshot = session.get_history()
        snapshot.append(Message(role="assistant", content="mutated"))

        self.assertEqual(len(snapshot), 2)
        self.assertEqual(session.get_history(), [Message(role="user", content="hello")])

    def test_clear_removes_only_in_memory_history(self) -> None:
        session = Session()
        session.add_user_message("hello")
        session.add_assistant_message("hi")

        session.clear()

        self.assertEqual(session.get_history(), [])

    def test_save_is_reserved_and_does_not_create_file(self) -> None:
        session = Session()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "history.json"

            with self.assertRaisesRegex(NotImplementedError, "Session.save"):
                session.save(target)

            self.assertFalse(target.exists())

    def test_compress_is_reserved(self) -> None:
        session = Session()

        with self.assertRaisesRegex(NotImplementedError, "Session.compress"):
            session.compress()


if __name__ == "__main__":
    unittest.main()
