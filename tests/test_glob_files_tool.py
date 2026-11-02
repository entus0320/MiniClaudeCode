import tempfile
import unittest
from pathlib import Path

from miniclaudecode.tools.base import ToolContext
from miniclaudecode.tools.glob_files import GlobFilesTool


class GlobFilesToolTests(unittest.TestCase):
    def test_matches_workspace_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.py").write_text("", encoding="utf-8")
            (root / "b.txt").write_text("", encoding="utf-8")
            result = GlobFilesTool().execute({"pattern": "*.py"}, ToolContext(root))

        self.assertTrue(result.success)
        self.assertIn("a.py", result.content)
        self.assertNotIn("b.txt", result.content)

    def test_rejects_absolute_pattern(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = GlobFilesTool().execute({"pattern": str(Path(directory) / "*.py")}, ToolContext(Path(directory)))

        self.assertFalse(result.success)
        self.assertIn("相对路径", result.error or "")

    def test_skips_sensitive_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env").write_text("secret", encoding="utf-8")
            result = GlobFilesTool().execute({"pattern": "*"}, ToolContext(root))

        self.assertTrue(result.success)
        self.assertNotIn(".env", result.content)


if __name__ == "__main__":
    unittest.main()
