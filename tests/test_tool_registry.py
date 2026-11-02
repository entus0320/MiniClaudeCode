import unittest
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from miniclaudecode.tools.base import Tool, ToolContext, ToolResult
from miniclaudecode.tools.defaults import create_default_tool_registry
from miniclaudecode.tools.registry import ToolRegistry, ToolRegistryError


class DummyTool(Tool):
    name = "dummy"
    description = "Dummy tool."
    input_schema = {"type": "object", "properties": {}, "additionalProperties": False}

    def execute(self, arguments: Mapping[str, Any], context: ToolContext) -> ToolResult:
        return ToolResult.ok(tool_name=self.name, content="ok")


class ToolRegistryTests(unittest.TestCase):
    def test_register_and_get_tool(self) -> None:
        registry = ToolRegistry()
        tool = DummyTool()

        registry.register(tool)

        self.assertIs(registry.get("dummy"), tool)
        self.assertEqual(registry.list_tools(), [tool])

    def test_rejects_duplicate_tool_name(self) -> None:
        registry = ToolRegistry()
        registry.register(DummyTool())

        with self.assertRaisesRegex(ToolRegistryError, "already registered"):
            registry.register(DummyTool())

    def test_unknown_tool_name_raises(self) -> None:
        registry = ToolRegistry()

        with self.assertRaisesRegex(ToolRegistryError, "Unknown tool"):
            registry.get("missing")

    def test_default_registry_contains_six_tools(self) -> None:
        registry = create_default_tool_registry()

        self.assertEqual(len(registry), 6)
        self.assertEqual(
            [tool.name for tool in registry.list_tools()],
            ["read_file", "write_file", "edit_file", "run_command", "glob_files", "grep_content"],
        )

    def test_default_tools_have_descriptions_and_strict_schemas(self) -> None:
        registry = create_default_tool_registry()

        for tool in registry.list_tools():
            self.assertTrue(tool.description)
            self.assertEqual(tool.input_schema["type"], "object")
            self.assertFalse(tool.input_schema["additionalProperties"])
            for required in tool.input_schema.get("required", []):
                self.assertIn(required, tool.input_schema["properties"])
            for parameter in tool.input_schema["properties"].values():
                self.assertTrue(parameter.get("description"))

    def test_default_tool_descriptions_reinforce_key_rules(self) -> None:
        tools = {tool.name: tool for tool in create_default_tool_registry().list_tools()}

        self.assertIn("Read-only", tools["read_file"].description)
        self.assertIn("workspace", tools["read_file"].description)
        self.assertIn("read_file", tools["edit_file"].description)
        self.assertIn("confirmation", tools["edit_file"].description)
        self.assertIn("read_file", tools["run_command"].description)
        self.assertIn("glob_files", tools["run_command"].description)
        self.assertIn("grep_content", tools["run_command"].description)
        self.assertIn("confirmation", tools["run_command"].description)
        self.assertIn("Prefer", tools["glob_files"].description)
        self.assertIn("Prefer", tools["grep_content"].description)


if __name__ == "__main__":
    unittest.main()
