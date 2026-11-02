import tempfile
import unittest
from pathlib import Path

from miniclaudecode.tools.base import ToolContext
from miniclaudecode.tools.grep_content import GrepContentTool


class GrepContentToolTests(unittest.TestCase):
    def test_finds_matching_line(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.py").write_text("def hello():\n    return 'pong'\n", encoding="utf-8")
            result = GrepContentTool().execute({"pattern": "hello", "glob": "*.py"}, ToolContext(root))

        self.assertTrue(result.success)
        self.assertIn("a.py:1", result.content)

    def test_invalid_regex_returns_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = GrepContentTool().execute({"pattern": "["}, ToolContext(Path(directory)))

        self.assertFalse(result.success)
        self.assertIn("正则", result.error or "")

    def test_skips_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env").write_text("OPENAI_API_KEY=secret", encoding="utf-8")
            result = GrepContentTool().execute({"pattern": "OPENAI"}, ToolContext(root))

        self.assertTrue(result.success)
        self.assertNotIn("secret", result.content)
        self.assertIn("未找到", result.content)


if __name__ == "__main__":
    unittest.main()
