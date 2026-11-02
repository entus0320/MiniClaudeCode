import json
import unittest
from collections.abc import Mapping
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from miniclaudecode.commands import CommandRegistry, create_builtin_command_registry
from miniclaudecode.skills import SkillLoader, SkillMode, SkillRegistry, SkillRuntime
from miniclaudecode.skills.core import SkillDiscoveryError, SkillValidationError
from miniclaudecode.skills.validator import normalize_skill_metadata, validate_skill_name
from miniclaudecode.tools.base import Tool, ToolContext, ToolResult
from miniclaudecode.tools.registry import ToolRegistry


class DummyTool(Tool):
    name = "read_file"
    description = "Read file."
    input_schema = {"type": "object", "properties": {}, "additionalProperties": False}

    def execute(self, arguments: Mapping[str, Any], context: ToolContext) -> ToolResult:
        return ToolResult.ok(tool_name=self.name, content="ok")


class OtherTool(Tool):
    name = "grep_content"
    description = "Search content."
    input_schema = {"type": "object", "properties": {}, "additionalProperties": False}

    def execute(self, arguments: Mapping[str, Any], context: ToolContext) -> ToolResult:
        return ToolResult.ok(tool_name=self.name, content="ok")


class SkillLoaderTests(unittest.TestCase):
    def test_file_skill_parses_frontmatter_and_body(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _write_skill(root / "commit.md", name="commit", body="Commit SOP")

            result = SkillLoader(project_root=root).discover()

        self.assertEqual(len(result.skills), 1)
        skill = result.skills[0]
        self.assertEqual(skill.name, "commit")
        self.assertEqual(skill.description, "commit description")
        self.assertEqual(skill.metadata.tools, ("read_file",))
        self.assertEqual(skill.metadata.mode, SkillMode.SHARED)
        self.assertEqual(skill.sop_markdown, "Commit SOP")

    def test_directory_skill_records_tool_resources(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            skill_dir = root / "demo"
            _write_skill(skill_dir / "skill.md", name="demo", body="Demo SOP")
            tools_dir = skill_dir / "tools"
            tools_dir.mkdir(parents=True)
            (tools_dir / "hello.json").write_text(
                json.dumps(
                    {
                        "name": "hello",
                        "description": "Say hello.",
                        "script": "hello.py",
                        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
                    }
                ),
                encoding="utf-8",
            )
            (tools_dir / "hello.py").write_text("print('{}')", encoding="utf-8")

            result = SkillLoader(project_root=root).discover()

        self.assertEqual(len(result.skills), 1)
        self.assertEqual(result.skills[0].source.package_kind.value, "directory")
        self.assertEqual(result.skills[0].directory_tools[0].name, "hello")

    def test_invalid_single_skill_is_skipped_with_warning(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "bad.md").write_text("---\ndescription: bad\n---\nbody", encoding="utf-8")
            _write_skill(root / "good.md", name="good", body="Good SOP")

            result = SkillLoader(project_root=root).discover()

        self.assertEqual([skill.name for skill in result.skills], ["good"])
        self.assertEqual(len(result.warnings), 1)
        self.assertIn("bad.md", str(result.warnings[0].path))

    def test_project_overrides_user_and_builtin_skill(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            builtin = root / "builtin"
            user = root / "user"
            project = root / "project"
            _write_skill(builtin / "same.md", name="same", body="builtin")
            _write_skill(user / "same.md", name="same", body="user")
            _write_skill(project / "same.md", name="same", body="project")

            result = SkillLoader(project_root=project, user_root=user, builtin_root=builtin).discover()

        self.assertEqual(len(result.skills), 1)
        self.assertEqual(result.skills[0].sop_markdown, "project")
        self.assertEqual(result.skills[0].source.kind.value, "project")
        self.assertEqual(result.overridden, ("same",))


class SkillValidationTests(unittest.TestCase):
    def test_rejects_invalid_skill_names(self) -> None:
        for name in ("bad name", "bad/name", "bad\\name"):
            with self.subTest(name=name):
                with self.assertRaises(SkillValidationError):
                    validate_skill_name(name)

    def test_unknown_mode_and_context_raise(self) -> None:
        with self.assertRaisesRegex(SkillValidationError, "未知 Skill mode"):
            normalize_skill_metadata({"name": "x", "description": "x", "mode": "bad"}, path=Path("x.md"))
        with self.assertRaisesRegex(SkillValidationError, "未知 Skill context"):
            normalize_skill_metadata({"name": "x", "description": "x", "context": "bad"}, path=Path("x.md"))

    def test_missing_whitelisted_tool_fails_fast(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _write_skill(root / "demo.md", name="demo", tools=("missing",), body="Demo")
            registry = SkillRegistry(SkillLoader(project_root=root), tool_registry=ToolRegistry())

            with self.assertRaisesRegex(SkillValidationError, "demo.*missing"):
                registry.load_initial()

    def test_builtin_command_name_conflict_fails_fast(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _write_skill(root / "help.md", name="help", body="Help")
            tools = ToolRegistry()
            registry = SkillRegistry(
                SkillLoader(project_root=root),
                tool_registry=tools,
                command_registry=create_builtin_command_registry(),
            )

            with self.assertRaisesRegex(SkillDiscoveryError, "/help"):
                registry.load_initial()


class SkillRuntimeTests(unittest.TestCase):
    def test_summary_instruction_omits_sop_and_activation_includes_sop(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _write_skill(root / "demo.md", name="demo", body="SECRET SOP")
            tool_registry = ToolRegistry()
            tool_registry.register(DummyTool())
            skill_registry = SkillRegistry(SkillLoader(project_root=root), tool_registry=tool_registry)
            skill_registry.load_initial()
            runtime = SkillRuntime(skill_registry)

            summary = runtime.summary_instruction()
            activation = runtime.activate("demo")
            active = runtime.active_instructions()[0]

        self.assertIsNotNone(summary)
        self.assertIn("/demo", summary.content)  # type: ignore[union-attr]
        self.assertNotIn("SECRET SOP", summary.content)  # type: ignore[union-attr]
        self.assertEqual(activation.message, "已激活 Skill：/demo。")
        self.assertIn("SECRET SOP", active.content)

    def test_isolated_mode_is_degraded_with_visible_message(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _write_skill(root / "iso.md", name="iso", mode="isolated", body="ISO SOP")
            tool_registry = ToolRegistry()
            tool_registry.register(DummyTool())
            skill_registry = SkillRegistry(SkillLoader(project_root=root), tool_registry=tool_registry)
            skill_registry.load_initial()

            result = SkillRuntime(skill_registry).activate("iso")

        self.assertTrue(result.isolated_mode_degraded)
        self.assertIn("隔离模式已预留", result.message)

    def test_visible_tools_use_union_of_active_whitelists(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _write_skill(root / "one.md", name="one", tools=("read_file",), body="One")
            _write_skill(root / "two.md", name="two", tools=("grep_content",), body="Two")
            tool_registry = ToolRegistry()
            read_tool = DummyTool()
            grep_tool = OtherTool()
            tool_registry.register(read_tool)
            tool_registry.register(grep_tool)
            skill_registry = SkillRegistry(SkillLoader(project_root=root), tool_registry=tool_registry)
            skill_registry.load_initial()
            runtime = SkillRuntime(skill_registry)
            runtime.activate("one")
            runtime.activate("two")

            visible = runtime.visible_tools([read_tool, grep_tool])

        self.assertEqual([tool.name for tool in visible], ["read_file", "grep_content"])

    def test_directory_tool_is_registered_and_requires_confirmation(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            skill_dir = root / "demo"
            _write_skill(skill_dir / "skill.md", name="demo", tools=(), body="Demo")
            tools_dir = skill_dir / "tools"
            tools_dir.mkdir(parents=True)
            (tools_dir / "hello.json").write_text(
                json.dumps(
                    {
                        "name": "hello",
                        "description": "Say hello.",
                        "script": "hello.py",
                        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
                    }
                ),
                encoding="utf-8",
            )
            (tools_dir / "hello.py").write_text(
                "import json\nprint(json.dumps({'success': True, 'content': 'hello'}))",
                encoding="utf-8",
            )
            tool_registry = ToolRegistry()
            skill_registry = SkillRegistry(SkillLoader(project_root=root), tool_registry=tool_registry)
            skill_registry.load_initial()

            tool = tool_registry.get("demo__hello")
            result = tool.execute({}, ToolContext(root))

        self.assertTrue(tool.requires_confirmation)
        self.assertTrue(result.success)
        self.assertEqual(result.content, "hello")


def _write_skill(
    path: Path,
    *,
    name: str,
    description: str | None = None,
    tools: tuple[str, ...] = ("read_file",),
    mode: str = "shared",
    context: str = "full-summary",
    body: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tools_text = "\n".join(f"  - {tool}" for tool in tools)
    path.write_text(
        f"---\n"
        f"name: {name}\n"
        f"description: {description or name + ' description'}\n"
        f"tools:\n{tools_text}\n"
        f"mode: {mode}\n"
        f"context: {context}\n"
        f"---\n\n"
        f"{body}",
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
