import tempfile
import unittest
from pathlib import Path

from miniclaudecode.tools.base import ToolContext
from miniclaudecode.tools.write_file import WriteFileTool


class WriteFileToolTests(unittest.TestCase):
    def test_writes_workspace_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = WriteFileTool().execute({"path": "note.txt", "content": "hello"}, ToolContext(root))

            self.assertTrue(result.success)
            self.assertEqual((root / "note.txt").read_text(encoding="utf-8"), "hello")

    def test_rejects_missing_parent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = WriteFileTool().execute({"path": "missing/note.txt", "content": "hello"}, ToolContext(root))

        self.assertFalse(result.success)
        self.assertIn("父目录", result.error or "")

    def test_rejects_sensitive_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = WriteFileTool().execute({"path": "config.yaml", "content": "secret"}, ToolContext(Path(directory)))

        self.assertFalse(result.success)
        self.assertIn("密钥", result.error or "")

    def test_rejects_policy_file_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = WriteFileTool().execute(
                {"path": "miniclaudecode.policy.yaml", "content": "mode: permissive"},
                ToolContext(Path(directory)),
            )

        self.assertFalse(result.success)
        self.assertIn("权限策略文件", result.error or "")


if __name__ == "__main__":
    unittest.main()
