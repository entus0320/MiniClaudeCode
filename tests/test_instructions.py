import tempfile
import unittest
from pathlib import Path

from miniclaudecode.instructions import InstructionConfig, InstructionDocument, InstructionBundle, load_instruction_bundle, render_instruction_bundle


class InstructionConfigTests(unittest.TestCase):
    def test_defaults(self) -> None:
        config = InstructionConfig()

        self.assertTrue(config.enabled)
        self.assertEqual(config.project_file, Path("MiniClaudeCode.md"))
        self.assertEqual(config.max_include_depth, 3)

    def test_disabled_returns_empty_bundle(self) -> None:
        config = InstructionConfig(enabled=False)

        bundle = load_instruction_bundle(config, workdir=Path("/tmp"))

        self.assertEqual(bundle.documents, ())
        self.assertEqual(bundle.warnings, ())

    def test_returns_project_and_user_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project_file = root / "MiniClaudeCode.md"
            project_file.write_text("# Project Rules\n\nBe strict.", encoding="utf-8")
            user_file = root / "user_instruction.md"
            user_file.write_text("# User Preferences\n\nPrefer Chinese.", encoding="utf-8")
            config = InstructionConfig(project_file=project_file, user_file=user_file, max_include_depth=3)

            bundle = load_instruction_bundle(config, workdir=root)

            self.assertEqual(len(bundle.documents), 2)
            self.assertEqual(bundle.documents[0].level, "project")
            self.assertEqual(bundle.documents[1].level, "user")
            self.assertIn("Project Rules", bundle.documents[0].content)
            self.assertIn("User Preferences", bundle.documents[1].content)

    def test_missing_project_file_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project_file = root / "missing.md"
            config = InstructionConfig(project_file=project_file, user_file=root / "also_missing.md")

            bundle = load_instruction_bundle(config, workdir=root)

            self.assertEqual(bundle.documents, ())

    def test_renders_project_before_user(self) -> None:
        documents = (
            InstructionDocument(level="project", path=Path("MiniClaudeCode.md"), content="项目规则。"),
            InstructionDocument(level="user", path=Path("user.md"), content="用户偏好。"),
        )
        bundle = InstructionBundle(documents=documents)

        text = render_instruction_bundle(bundle)

        project_index = text.index("项目规则。")
        user_index = text.index("用户偏好。")
        self.assertLess(project_index, user_index)
        self.assertIn("项目级 > 用户级", text)


class IncludeExpansionTests(unittest.TestCase):
    def test_include_pulls_content_from_relative_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root_file = root / "root.md"
            root_file.write_text("@include sub/child.md\nroot line", encoding="utf-8")
            sub = root / "sub"
            sub.mkdir()
            child = sub / "child.md"
            child.write_text("child content", encoding="utf-8")
            config = InstructionConfig(project_file=root_file, max_include_depth=3)

            bundle = load_instruction_bundle(config, workdir=root)

            self.assertEqual(len(bundle.documents), 1)
            self.assertIn("child content", bundle.documents[0].content)
            self.assertIn("root line", bundle.documents[0].content)

    def test_include_absolute_path_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root_file = root / "root.md"
            # Use a path that is absolute on the current platform
            abs_path = str(root.resolve() / "outside.md")
            root_file.write_text(f"@include {abs_path}\nsafe", encoding="utf-8")
            config = InstructionConfig(project_file=root_file, max_include_depth=3)

            bundle = load_instruction_bundle(config, workdir=root)

            self.assertIn("safe", bundle.documents[0].content)
            self.assertTrue(any("绝对路径" in warning for warning in bundle.warnings))

    def test_include_parent_directory_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root_file = root / "root.md"
            root_file.write_text("@include ../outside.md\nsafe", encoding="utf-8")
            config = InstructionConfig(project_file=root_file, max_include_depth=3)

            bundle = load_instruction_bundle(config, workdir=root)

            self.assertIn("safe", bundle.documents[0].content)
            self.assertTrue(any(".." in warning for warning in bundle.warnings))

    def test_include_beyond_max_depth_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            a = root / "level1.md"
            sub = root / "sub"
            sub.mkdir()
            b = sub / "level2.md"
            deeper = sub / "deeper"
            deeper.mkdir()
            c = deeper / "level3.md"
            b.write_text("@include deeper/level3.md\nlevel2", encoding="utf-8")
            a.write_text("@include sub/level2.md\nlevel1", encoding="utf-8")
            c.write_text("level3", encoding="utf-8")
            config = InstructionConfig(project_file=a, max_include_depth=1)

            bundle = load_instruction_bundle(config, workdir=root)

            self.assertIn("level1", bundle.documents[0].content)
            self.assertNotIn("level3", bundle.documents[0].content)
            self.assertTrue(any("超过最大深度" in warning for warning in bundle.warnings))

    def test_cyclic_include_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            a = root / "a.md"
            b = root / "b.md"
            a.write_text("@include b.md\na", encoding="utf-8")
            b.write_text("@include a.md\nb", encoding="utf-8")
            config = InstructionConfig(project_file=a, max_include_depth=3)

            bundle = load_instruction_bundle(config, workdir=root)

            self.assertIn("a", bundle.documents[0].content)
            self.assertIn("b", bundle.documents[0].content)
            self.assertTrue(any("循环" in warning for warning in bundle.warnings))

    def test_empty_include_value_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root_file = root / "root.md"
            root_file.write_text('@include ""\nsafe', encoding="utf-8")
            config = InstructionConfig(project_file=root_file, max_include_depth=3)

            bundle = load_instruction_bundle(config, workdir=root)

            self.assertIn("safe", bundle.documents[0].content)
            self.assertTrue(any("空 include" in warning for warning in bundle.warnings))


class PromptModuleConversionTests(unittest.TestCase):
    def test_empty_bundle_returns_none(self) -> None:
        bundle = InstructionBundle()

        module = bundle.to_prompt_module()

        self.assertIsNone(module)

    def test_non_empty_bundle_returns_dynamic_module(self) -> None:
        documents = (InstructionDocument(level="project", path=Path("MiniClaudeCode.md"), content="规则。"),)
        bundle = InstructionBundle(documents=documents)

        module = bundle.to_prompt_module()

        self.assertIsNotNone(module)
        self.assertEqual(module.name, "long_term_instructions")
        self.assertEqual(module.priority, 80)


if __name__ == "__main__":
    unittest.main()
