import json
import tempfile
import unittest
from pathlib import Path

from miniclaudecode.memory import MemoryManager, MemoryUpdateResult, USER_PREFERENCES, FEEDBACK, PROJECT_KNOWLEDGE, REFERENCES, MEMORY_INDEX
from miniclaudecode.providers.base import BaseProvider, ProviderTurnComplete
from miniclaudecode.session import Message, Session


class FakeMemoryProvider(BaseProvider):
    def __init__(self, response_text: str = "", *, fail: bool = False) -> None:
        self.response_text = response_text
        self.fail = fail
        self.requests: list[list[Message]] = []

    def stream_chat(self, messages, prompt=None):  # type: ignore[no-untyped-def]
        return iter(())

    def stream_turn(self, messages, tools=None, prompt=None):  # type: ignore[no-untyped-def]
        self.requests.append(list(messages))
        if self.fail:
            raise RuntimeError("provider failure")
        yield ProviderTurnComplete(text=self.response_text)


class MemoryManagerTests(unittest.TestCase):
    def test_disabled_returns_none_on_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            mgr = MemoryManager(user_dir=Path(directory) / "user", project_dir=Path(directory) / "project", enabled=False)

            result = mgr.record_user_turn(FakeMemoryProvider(), Session())

            self.assertIsNone(result)

    def test_no_trigger_before_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            mgr = MemoryManager(
                user_dir=Path(directory) / "user",
                project_dir=Path(directory) / "project",
                auto_every_turns=5,
                enabled=True,
            )
            provider = FakeMemoryProvider()

            result = None
            for _ in range(4):
                result = mgr.record_user_turn(provider, Session())

            self.assertIsNone(result)
            self.assertEqual(len(provider.requests), 0)

    def test_trigger_at_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            mgr = MemoryManager(
                user_dir=Path(directory) / "user",
                project_dir=Path(directory) / "project",
                auto_every_turns=3,
                enabled=True,
            )
            payload = {
                "user_preferences_md": "偏好中文",
                "feedback_md": "反馈",
                "project_knowledge_md": "项目知识",
                "references_md": "参考",
            }
            provider = FakeMemoryProvider(json.dumps(payload, ensure_ascii=False))

            result = None
            for _ in range(3):
                result = mgr.record_user_turn(provider, Session())

            self.assertIsNotNone(result)
            self.assertTrue(result.updated)
            self.assertIn("auto_every_turns", result.reason)

    def test_update_prompt_requires_explicit_user_preference_capture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            user_dir = Path(directory) / "user"
            project_dir = Path(directory) / "project"
            mgr = MemoryManager(user_dir=user_dir, project_dir=project_dir, enabled=True)
            session = Session()
            session.add_user_message("我喜欢打篮球")

            prompt = mgr._build_update_prompt(session.get_history())

            self.assertIn("用户喜欢打篮球", prompt)
            self.assertIn("必须合并进 user_preferences_md", prompt)
            self.assertIn("我喜欢打篮球", prompt)

    def test_manual_update_writes_notes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            user_dir = Path(directory) / "user"
            project_dir = Path(directory) / "project"
            mgr = MemoryManager(user_dir=user_dir, project_dir=project_dir, enabled=True)
            payload = {
                "user_preferences_md": "偏好中文回答",
                "feedback_md": "上次建议太啰嗦",
                "project_knowledge_md": "这是一个 Python 项目",
                "references_md": "参考 Claude Code 文档",
            }
            provider = FakeMemoryProvider(json.dumps(payload, ensure_ascii=False))
            session = Session()
            session.add_user_message("记住：我喜欢中文回答")

            result = mgr.update_now(provider, session)

            self.assertTrue(result.updated)
            self.assertIn("偏好中文回答", (user_dir / USER_PREFERENCES).read_text(encoding="utf-8"))
            self.assertIn("上次建议太啰嗦", (user_dir / FEEDBACK).read_text(encoding="utf-8"))
            self.assertIn("这是一个 Python 项目", (project_dir / PROJECT_KNOWLEDGE).read_text(encoding="utf-8"))
            self.assertIn("参考 Claude Code 文档", (project_dir / REFERENCES).read_text(encoding="utf-8"))

    def test_invalid_json_does_not_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            user_dir = Path(directory) / "user"
            project_dir = Path(directory) / "project"
            (project_dir / PROJECT_KNOWLEDGE).parent.mkdir(parents=True, exist_ok=True)
            (project_dir / PROJECT_KNOWLEDGE).write_text("existing knowledge", encoding="utf-8")
            mgr = MemoryManager(user_dir=user_dir, project_dir=project_dir, enabled=True)
            provider = FakeMemoryProvider("not valid json")
            session = Session()
            session.add_user_message("test")

            result = mgr.update_now(provider, session)

            self.assertFalse(result.updated)
            self.assertIsNotNone(result.error)
            self.assertEqual((project_dir / PROJECT_KNOWLEDGE).read_text(encoding="utf-8"), "existing knowledge")

    def test_missing_field_does_not_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            user_dir = Path(directory) / "user"
            project_dir = Path(directory) / "project"
            (project_dir / PROJECT_KNOWLEDGE).parent.mkdir(parents=True, exist_ok=True)
            (project_dir / PROJECT_KNOWLEDGE).write_text("existing", encoding="utf-8")
            mgr = MemoryManager(user_dir=user_dir, project_dir=project_dir, enabled=True)
            payload = {
                "user_preferences_md": "ok",
                "feedback_md": "ok",
                "project_knowledge_md": "ok",
            }
            provider = FakeMemoryProvider(json.dumps(payload, ensure_ascii=False))
            session = Session()

            result = mgr.update_now(provider, session)

            self.assertFalse(result.updated)
            self.assertIn("references_md", result.error or "")

    def test_provider_failure_returns_error_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            mgr = MemoryManager(user_dir=Path(directory) / "user", project_dir=Path(directory) / "project", enabled=True)
            provider = FakeMemoryProvider(fail=True)
            session = Session()

            result = mgr.update_now(provider, session)

            self.assertFalse(result.updated)
            self.assertIn("provider failure", result.error or "")

    def test_note_paths_match_document_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            user_dir = Path(directory) / "user"
            project_dir = Path(directory) / "project"
            mgr = MemoryManager(user_dir=user_dir, project_dir=project_dir)

            paths = mgr.note_paths()

            self.assertEqual(paths["user_preferences"], (user_dir / USER_PREFERENCES).expanduser())
            self.assertEqual(paths["feedback"], (user_dir / FEEDBACK).expanduser())
            self.assertEqual(paths["project_knowledge"], project_dir / PROJECT_KNOWLEDGE)
            self.assertEqual(paths["references"], project_dir / REFERENCES)

    def test_list_notes_shows_existing_notes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            user_dir = Path(directory) / "user"
            project_dir = Path(directory) / "project"
            (project_dir / PROJECT_KNOWLEDGE).parent.mkdir(parents=True, exist_ok=True)
            (project_dir / PROJECT_KNOWLEDGE).write_text("existing", encoding="utf-8")
            mgr = MemoryManager(user_dir=user_dir, project_dir=project_dir)

            notes = mgr.list_notes()

            self.assertEqual(len(notes), 1)
            self.assertIn(project_dir / PROJECT_KNOWLEDGE, notes)

    def test_load_all_content_returns_combined_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            user_dir = Path(directory) / "user"
            project_dir = Path(directory) / "project"
            (project_dir / PROJECT_KNOWLEDGE).parent.mkdir(parents=True, exist_ok=True)
            (project_dir / PROJECT_KNOWLEDGE).write_text("# 项目知识\n\n这是测试项目。", encoding="utf-8")
            (user_dir / USER_PREFERENCES).parent.mkdir(parents=True, exist_ok=True)
            (user_dir / USER_PREFERENCES).write_text("# 用户偏好\n\n偏好中文。", encoding="utf-8")
            mgr = MemoryManager(user_dir=user_dir, project_dir=project_dir)

            content = mgr.load_all_content()

            self.assertIn("项目知识", content)
            self.assertIn("这是测试项目", content)
            self.assertIn("用户偏好", content)
            self.assertIn("偏好中文", content)
            self.assertIn("<system-reminder>", content)

    def test_load_all_content_disabled_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            mgr = MemoryManager(
                user_dir=Path(directory) / "user",
                project_dir=Path(directory) / "project",
                enabled=False,
            )

            self.assertEqual(mgr.load_all_content(), "")

    def test_to_prompt_module_returns_none_when_empty(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            mgr = MemoryManager(
                user_dir=Path(directory) / "user",
                project_dir=Path(directory) / "project",
                enabled=True,
            )

            module = mgr.to_prompt_module()

            self.assertIsNone(module)

    def test_to_prompt_module_returns_module_when_notes_exist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            user_dir = Path(directory) / "user"
            project_dir = Path(directory) / "project"
            (project_dir / PROJECT_KNOWLEDGE).parent.mkdir(parents=True, exist_ok=True)
            (project_dir / PROJECT_KNOWLEDGE).write_text("# 项目知识\n\n内容。", encoding="utf-8")
            mgr = MemoryManager(user_dir=user_dir, project_dir=project_dir)

            module = mgr.to_prompt_module()

            self.assertIsNotNone(module)
            self.assertEqual(module.name, "long_term_memory")
            self.assertEqual(module.priority, 85)
            self.assertIn("内容。", module.content)

    def test_write_index_creates_memory_index_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            user_dir = Path(directory) / "user"
            project_dir = Path(directory) / "project"
            (project_dir / PROJECT_KNOWLEDGE).parent.mkdir(parents=True, exist_ok=True)
            (project_dir / PROJECT_KNOWLEDGE).write_text("# 项目技术栈\n\nPython 项目。", encoding="utf-8")
            (project_dir / REFERENCES).write_text("# 参考\n\nREADME.md", encoding="utf-8")
            (user_dir / USER_PREFERENCES).parent.mkdir(parents=True, exist_ok=True)
            (user_dir / USER_PREFERENCES).write_text("# 我的偏好\n\n中文。", encoding="utf-8")
            mgr = MemoryManager(user_dir=user_dir, project_dir=project_dir)

            mgr._write_index()

            user_index = user_dir / MEMORY_INDEX
            project_index = project_dir / MEMORY_INDEX
            self.assertTrue(user_index.exists())
            self.assertTrue(project_index.exists())
            user_content = user_index.read_text(encoding="utf-8")
            project_content = project_index.read_text(encoding="utf-8")
            self.assertIn("用户偏好", user_content)
            self.assertIn("user-preferences.md", user_content)
            self.assertIn("项目知识", project_content)
            self.assertIn("project-knowledge.md", project_content)


if __name__ == "__main__":
    unittest.main()
