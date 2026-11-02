import tempfile
import unittest
from pathlib import Path

from miniclaudecode.tools.base import ToolContext
from miniclaudecode.tools.read_file import ReadFileTool


class ReadFileToolTests(unittest.TestCase):
    def test_reads_workspace_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "note.txt").write_text("hello", encoding="utf-8")
            result = ReadFileTool().execute({"path": "note.txt"}, ToolContext(root))

        self.assertTrue(result.success)
        self.assertEqual(result.content, "hello")
        self.assertEqual(result.metadata["path"], "note.txt")

    def test_rejects_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = ReadFileTool().execute({"path": "missing.txt"}, ToolContext(Path(directory)))

        self.assertFalse(result.success)
        self.assertIn("不存在", result.error or "")

    def test_rejects_outside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = ReadFileTool().execute({"path": "../outside.txt"}, ToolContext(Path(directory)))

        self.assertFalse(result.success)
        self.assertIn("工作目录", result.error or "")

    def test_rejects_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env").write_text("OPENAI_API_KEY=secret", encoding="utf-8")
            result = ReadFileTool().execute({"path": ".env"}, ToolContext(root))

        self.assertFalse(result.success)
        self.assertIn("密钥", result.error or "")


if __name__ == "__main__":
    unittest.main()
