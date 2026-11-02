import tempfile
import unittest
from pathlib import Path

from miniclaudecode.tools.base import ToolContext
from miniclaudecode.tools.edit_file import EditFileTool


class EditFileToolTests(unittest.TestCase):
    def test_replaces_unique_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "note.txt"
            target.write_text("hello world", encoding="utf-8")
            result = EditFileTool().execute(
                {"path": "note.txt", "old_text": "world", "new_text": "MiniClaudeCode"}, ToolContext(root)
            )

            self.assertTrue(result.success)
            self.assertEqual(target.read_text(encoding="utf-8"), "hello MiniClaudeCode")

    def test_fails_when_text_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "note.txt").write_text("hello", encoding="utf-8")
            result = EditFileTool().execute(
                {"path": "note.txt", "old_text": "missing", "new_text": "x"}, ToolContext(root)
            )

        self.assertFalse(result.success)
        self.assertIn("匹配不到", result.error or "")

    def test_fails_when_text_matches_multiple_times(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "note.txt").write_text("x x", encoding="utf-8")
            result = EditFileTool().execute({"path": "note.txt", "old_text": "x", "new_text": "y"}, ToolContext(root))

        self.assertFalse(result.success)
        self.assertIn("匹配多次", result.error or "")


if __name__ == "__main__":
    unittest.main()
