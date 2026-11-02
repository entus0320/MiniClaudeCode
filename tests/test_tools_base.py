import unittest
from pathlib import Path

from miniclaudecode.tools.base import ToolContext, ToolResult, sanitize_text


class ToolResultTests(unittest.TestCase):
    def test_tool_result_serializes_for_model(self) -> None:
        result = ToolResult.ok(tool_name="read_file", content="hello", metadata={"path": "README.md"})

        self.assertIn('"success": true', result.to_model_text())
        self.assertIn("README.md", result.to_model_text())

    def test_failed_result_is_error(self) -> None:
        result = ToolResult.fail(tool_name="read_file", error="missing")

        self.assertTrue(result.is_error)
        self.assertIn("missing", result.to_model_text())

    def test_sanitize_text_redacts_known_secret_and_key_shape(self) -> None:
        context = ToolContext(Path.cwd(), secrets=("secret-value",))

        text = sanitize_text("secret-value sk-ant-abc123456", context)

        self.assertNotIn("secret-value", text)
        self.assertNotIn("sk-ant-abc123456", text)
        self.assertIn("[REDACTED]", text)


if __name__ == "__main__":
    unittest.main()
