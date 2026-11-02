import unittest
from typing import Any

from miniclaudecode.mcp.client import McpCallResult, McpToolDescription
from miniclaudecode.mcp.tools import McpTool, mcp_tool_name
from miniclaudecode.tools.base import ToolContext


class FakeManager:
    def __init__(self, result: McpCallResult) -> None:
        self.result = result
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> McpCallResult:
        self.calls.append((server_name, tool_name, arguments))
        return self.result


class McpToolTests(unittest.TestCase):
    def test_mcp_tool_name_uses_server_prefix(self) -> None:
        self.assertEqual(mcp_tool_name("fake", "echo"), "fake__echo")

    def test_mcp_tool_defaults_to_confirmation_and_executes(self) -> None:
        description = McpToolDescription(
            server_name="fake",
            name="echo",
            description="Echo text.",
            input_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        )
        manager = FakeManager(McpCallResult(success=True, content="hello secret"))
        tool = McpTool(description, manager)

        result = tool.execute({"text": "hello"}, ToolContext(".", secrets=("secret",)))

        self.assertTrue(tool.requires_confirmation)
        self.assertFalse(tool.parallel_safe)
        self.assertEqual(tool.name, "fake__echo")
        self.assertEqual(manager.calls, [("fake", "echo", {"text": "hello"})])
        self.assertTrue(result.success)
        self.assertEqual(result.content, "hello [REDACTED]")

    def test_default_output_limit_allows_large_mcp_output_to_reach_context_slimmer(self) -> None:
        description = McpToolDescription(
            server_name="fake",
            name="large_echo",
            description="Echo large text.",
            input_schema={"type": "object", "properties": {}},
        )
        manager = FakeManager(McpCallResult(success=True, content="x" * 30000))
        tool = McpTool(description, manager)

        result = tool.execute({}, ToolContext("."))

        self.assertTrue(result.success)
        self.assertGreater(len(result.content), 24000)
        self.assertNotIn("[输出已截断]", result.content)

    def test_mcp_tool_parallel_safe_comes_from_description(self) -> None:
        description = McpToolDescription(
            server_name="fake",
            name="safe_echo",
            description="Echo text.",
            input_schema={"type": "object", "properties": {}},
            parallel_safe=True,
        )
        tool = McpTool(description, FakeManager(McpCallResult(success=True)))

        self.assertTrue(tool.parallel_safe)

    def test_mcp_tool_failure_is_sanitized(self) -> None:
        description = McpToolDescription(
            server_name="fake",
            name="echo",
            description="Echo text.",
            input_schema={"type": "object", "properties": {}},
        )
        manager = FakeManager(McpCallResult(success=False, error="bad secret"))
        tool = McpTool(description, manager)

        result = tool.execute({}, ToolContext(".", secrets=("secret",)))

        self.assertFalse(result.success)
        self.assertEqual(result.error, "bad [REDACTED]")

    def test_invalid_schema_is_rejected(self) -> None:
        description = McpToolDescription(
            server_name="fake",
            name="echo",
            description="Echo text.",
            input_schema={"type": "string"},
        )

        with self.assertRaisesRegex(ValueError, "object schema"):
            McpTool(description, FakeManager(McpCallResult(success=True)))


if __name__ == "__main__":
    unittest.main()
