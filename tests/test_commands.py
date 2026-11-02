import unittest
from collections.abc import Mapping
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from miniclaudecode.commands import (
    Command,
    CommandParser,
    CommandRegistrationError,
    CommandRegistry,
    CommandType,
    create_builtin_command_registry,
)
from miniclaudecode.skills import SkillLoader, SkillRegistry, SkillRuntime, register_skill_commands
from miniclaudecode.tools.base import Tool, ToolContext, ToolResult
from miniclaudecode.tools.registry import ToolRegistry


class DummyTool(Tool):
    name = "read_file"
    description = "Read file."
    input_schema = {"type": "object", "properties": {}, "additionalProperties": False}

    def execute(self, arguments: Mapping[str, Any], context: ToolContext) -> ToolResult:
        return ToolResult.ok(tool_name=self.name, content="ok")


class CommandRegistryTests(unittest.TestCase):
    def test_register_and_lookup_command_by_name_and_alias(self) -> None:
        registry = CommandRegistry()
        command = Command(name="session", aliases=("s",), description="show session")

        registry.register(command)

        self.assertEqual(registry.get("session").name, "session")  # type: ignore[union-attr]
        self.assertEqual(registry.get("/SESSION").name, "session")  # type: ignore[union-attr]
        self.assertEqual(registry.get("s").name, "session")  # type: ignore[union-attr]

    def test_duplicate_name_raises(self) -> None:
        registry = CommandRegistry()
        registry.register(Command(name="session"))

        with self.assertRaises(CommandRegistrationError):
            registry.register(Command(name="SESSION"))

    def test_duplicate_alias_raises(self) -> None:
        registry = CommandRegistry()
        registry.register(Command(name="session", aliases=("s",)))

        with self.assertRaises(CommandRegistrationError):
            registry.register(Command(name="sessions", aliases=("S",)))

    def test_alias_conflicts_with_existing_name(self) -> None:
        registry = CommandRegistry()
        registry.register(Command(name="session"))

        with self.assertRaises(CommandRegistrationError):
            registry.register(Command(name="show", aliases=("session",)))

    def test_alias_cannot_duplicate_own_name(self) -> None:
        registry = CommandRegistry()

        with self.assertRaises(CommandRegistrationError):
            registry.register(Command(name="session", aliases=("session",)))

    def test_hidden_commands_are_not_visible_or_completed(self) -> None:
        registry = CommandRegistry()
        registry.register(Command(name="help"))
        registry.register(Command(name="allow", hidden=True))

        self.assertEqual([command.name for command in registry.visible_commands()], ["help"])
        self.assertNotIn("/allow", [item.value for item in registry.completions("a")])

    def test_completions_include_aliases(self) -> None:
        registry = CommandRegistry()
        registry.register(Command(name="exit", aliases=("quit",)))

        completions = registry.completions("q")

        self.assertEqual(completions[0].value, "/quit")
        self.assertTrue(completions[0].is_alias)

    def test_slash_prefix_returns_all_visible_builtin_completions(self) -> None:
        registry = create_builtin_command_registry()

        values = [item.value for item in registry.completions("")]

        self.assertIn("/help", values)
        self.assertIn("/compact", values)
        self.assertNotIn("/allow", values)

    def test_se_prefix_returns_session_commands(self) -> None:
        registry = create_builtin_command_registry()

        values = [item.value for item in registry.completions("se")]

        self.assertIn("/session", values)
        self.assertIn("/sessions", values)

    def test_builtin_registry_contains_prompt_type_support(self) -> None:
        self.assertEqual(CommandType.PROMPT.value, "prompt")

    def test_skill_command_registration_participates_in_lookup_and_completion(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _write_skill(root / "demo.md")
            tools = ToolRegistry()
            tools.register(DummyTool())
            commands = create_builtin_command_registry()
            skill_registry = SkillRegistry(SkillLoader(project_root=root), tool_registry=tools, command_registry=commands)
            skill_registry.load_initial()
            runtime = SkillRuntime(skill_registry)

            register_skill_commands(commands, skill_registry, runtime)

        self.assertEqual(commands.get("demo").name, "demo")  # type: ignore[union-attr]
        self.assertIn("/demo", [item.value for item in commands.completions("d")])


class CommandParserTests(unittest.TestCase):
    def test_parse_resume_with_args(self) -> None:
        invocation = CommandParser().parse("/resume abc def")

        self.assertIsNotNone(invocation)
        self.assertEqual(invocation.name, "resume")  # type: ignore[union-attr]
        self.assertEqual(invocation.args, "abc def")  # type: ignore[union-attr]

    def test_parse_is_case_insensitive(self) -> None:
        invocation = CommandParser().parse("/SESSION")

        self.assertIsNotNone(invocation)
        self.assertEqual(invocation.name, "session")  # type: ignore[union-attr]

    def test_non_slash_input_is_not_command(self) -> None:
        self.assertIsNone(CommandParser().parse("hello"))

    def test_completion_prefix_ignores_arguments(self) -> None:
        parser = CommandParser()

        self.assertEqual(parser.completion_prefix("/ses"), "ses")
        self.assertIsNone(parser.completion_prefix("/resume abc"))


def _write_skill(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        "name: demo\n"
        "description: demo skill\n"
        "tools:\n"
        "  - read_file\n"
        "mode: shared\n"
        "context: full-summary\n"
        "---\n\n"
        "Demo SOP",
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
