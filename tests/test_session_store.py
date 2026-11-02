import json
import tempfile
import unittest
from pathlib import Path

from miniclaudecode.session import Message
from miniclaudecode.session_store import (
    SessionStore,
    SessionMeta,
    LoadedSession,
    new_session_id,
    utc_now_iso,
    message_to_dict,
    message_from_dict,
    tool_call_to_dict,
    tool_call_from_dict,
    load_messages_from_jsonl,
    truncate_incomplete_tool_groups,
)
from miniclaudecode.tools.base import ToolCall


class SessionStoreTests(unittest.TestCase):
    def test_creates_session_directory_and_meta(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "sessions"
            store = SessionStore(root, provider_name="test", model="test-model")

            self.assertTrue(store.session_dir.exists())
            self.assertTrue(store.meta_path.exists())
            self.assertIn("test", store._meta.provider_name)

    def test_disabled_store_does_not_create_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "sessions"
            store = SessionStore(root, provider_name="test", model="test-model", enabled=False)

            self.assertFalse(store.session_dir.exists())

    def test_record_message_increments_count_and_writes_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "sessions"
            store = SessionStore(root, provider_name="test", model="test-model")
            msg = Message(role="user", content="hello")

            store.record_message(msg)

            self.assertEqual(store._message_count, 1)
            self.assertEqual(store._event_count, 1)
            content = store.transcript_path.read_text(encoding="utf-8")
            events = [json.loads(line) for line in content.strip().split("\n")]
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["type"], "message")
            self.assertEqual(events[0]["message"]["role"], "user")

    def test_record_history_replaced_resets_message_count(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "sessions"
            store = SessionStore(root, provider_name="test", model="test-model")
            msg1 = Message(role="user", content="a")
            msg2 = Message(role="assistant", content="b")
            store.record_message(msg1)
            store.record_message(msg2)

            store.record_history_replaced([msg1])

            self.assertEqual(store._message_count, 1)

    def test_disabled_does_not_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "sessions"
            store = SessionStore(root, provider_name="test", model="test-model", enabled=False)
            msg = Message(role="user", content="hello")

            store.record_message(msg)

            self.assertEqual(store._message_count, 0)

    def test_list_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "sessions"
            store1 = SessionStore(root, provider_name="test", model="test-model")
            store1.record_message(Message(role="user", content="first"))
            store2 = SessionStore(root, provider_name="test", model="test-model", session_id="custom-id")
            store2.record_message(Message(role="user", content="second"))

            sessions = store1.list_sessions()

            self.assertEqual(len(sessions), 2)
            ids = {s.session_id for s in sessions}
            self.assertIn(store1.session_id, ids)
            self.assertIn("custom-id", ids)

    def test_load_session_recovers_messages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "sessions"
            store = SessionStore(root, provider_name="test", model="test-model")
            msg = Message(role="user", content="hello")
            store.record_message(msg)

            loaded = store.load_session(store.session_id)

            self.assertEqual(len(loaded.messages), 1)
            self.assertEqual(loaded.messages[0].role, "user")
            self.assertEqual(loaded.messages[0].content, "hello")

    def test_load_session_tolerates_bad_jsonl_lines(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "sessions"
            store = SessionStore(root, provider_name="test", model="test-model")
            store.record_message(Message(role="user", content="good"))
            with store.transcript_path.open("a", encoding="utf-8") as file:
                file.write("this is not json\n")

            loaded = store.load_session(store.session_id)

            self.assertEqual(len(loaded.messages), 1)
            self.assertIn("无法解析的 JSONL 行", "\n".join(loaded.warnings))


class SerializationTests(unittest.TestCase):
    def test_message_roundtrip(self) -> None:
        msg = Message(
            role="assistant",
            content="text",
            tool_calls=[ToolCall(id="call-1", name="read_file", arguments={"path": "x.txt"})],
            tool_call_id=None,
            tool_name=None,
            synthetic_kind=None,
            tool_result_offload=None,
        )

        roundtripped = message_from_dict(message_to_dict(msg))

        self.assertEqual(roundtripped.role, msg.role)
        self.assertEqual(roundtripped.content, msg.content)
        self.assertEqual(len(roundtripped.tool_calls), 1)
        self.assertEqual(roundtripped.tool_calls[0].id, "call-1")

    def test_tool_call_roundtrip(self) -> None:
        call = ToolCall(id="c1", name="read_file", arguments={"path": "x.txt"}, raw_arguments='{"path":"x.txt"}', provider="openai")

        roundtripped = tool_call_from_dict(tool_call_to_dict(call))

        self.assertEqual(roundtripped.id, call.id)
        self.assertEqual(roundtripped.name, call.name)
        self.assertEqual(roundtripped.arguments, call.arguments)
        self.assertEqual(roundtripped.raw_arguments, call.raw_arguments)

    def test_message_with_synthetic_kind(self) -> None:
        msg = Message(role="user", content="summary text", synthetic_kind="context_summary")

        encoded = message_to_dict(msg)
        decoded = message_from_dict(encoded)

        self.assertEqual(decoded.synthetic_kind, "context_summary")


class RecoveryTests(unittest.TestCase):
    def test_truncate_incomplete_tool_use(self) -> None:
        messages = [
            Message(role="user", content="read"),
            Message(role="assistant", content="", tool_calls=[ToolCall(id="call-1", name="read_file")]),
        ]

        recovered, warning = truncate_incomplete_tool_groups(messages)

        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0].role, "user")
        self.assertIsNotNone(warning)
        self.assertIn("未配对", warning)

    def test_truncate_orphan_tool_result(self) -> None:
        messages = [
            Message(role="user", content="read"),
            Message(role="tool", content="result", tool_call_id="call-1", tool_name="read"),
        ]

        recovered, warning = truncate_incomplete_tool_groups(messages)

        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0].role, "user")
        self.assertIsNotNone(warning)
        self.assertIn("孤立", warning)

    def test_full_tool_group_is_preserved(self) -> None:
        messages = [
            Message(role="user", content="read"),
            Message(role="assistant", content="", tool_calls=[ToolCall(id="call-1", name="read_file")]),
            Message(role="tool", content="result", tool_call_id="call-1", tool_name="read_file"),
            Message(role="assistant", content="done"),
        ]

        recovered, warning = truncate_incomplete_tool_groups(messages)

        self.assertEqual(len(recovered), 4)
        self.assertIsNone(warning)

    def test_load_messages_skips_bad_lines(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "transcript.jsonl"
            store = SessionStore(Path(directory) / "sessions", provider_name="t", model="m", session_id="sid")
            store.record_message(Message(role="user", content="good"))
            with open(path, "w", encoding="utf-8") as file:
                file.write("not json\n")
                file.write(json.dumps({
                    "schema_version": 1,
                    "seq": 2,
                    "timestamp": "2026-01-01T00:00:00",
                    "type": "message",
                    "message": {"role": "user", "content": "ok"}
                }) + "\n")

            messages, warnings = load_messages_from_jsonl(path)

            self.assertGreaterEqual(len(messages), 1)
            self.assertTrue(any("无法解析的 JSONL 行" in w for w in warnings))


class MetaTests(unittest.TestCase):
    def test_first_message_sets_title(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "sessions"
            store = SessionStore(root, provider_name="test", model="test-model")
            store.record_message(Message(role="user", content="帮我修复登录 bug"))

            self.assertEqual(store._meta.title, "帮我修复登录 bug")

    def test_meta_atomic_update(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "sessions"
            store = SessionStore(root, provider_name="test", model="test-model")

            loaded_meta = json.loads(store.meta_path.read_text(encoding="utf-8"))

            self.assertEqual(loaded_meta["message_count"], 0)
            self.assertEqual(loaded_meta["provider_name"], "test")


if __name__ == "__main__":
    unittest.main()
