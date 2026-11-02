import unittest
from pathlib import Path

from miniclaudecode.prompt_modules import default_prompt_modules
from miniclaudecode.prompting import (
    DynamicInstruction,
    PromptAssembler,
    PromptBlock,
    PromptCachePolicy,
    PromptModule,
    PromptStability,
    RuntimeContext,
    plan_only_instruction,
)


class PromptingTests(unittest.TestCase):
    def test_modules_are_sorted_deterministically(self) -> None:
        modules = [
            PromptModule("zeta", 20, PromptStability.STABLE, "Z"),
            PromptModule("alpha", 10, PromptStability.STABLE, "A"),
            PromptModule("beta", 10, PromptStability.STABLE, "B"),
        ]

        prompt = PromptAssembler(modules).assemble()

        self.assertEqual([block.name for block in prompt.stable_blocks], ["alpha", "beta", "zeta"])

    def test_runtime_context_is_dynamic_not_stable(self) -> None:
        context = RuntimeContext(
            workdir=Path("/tmp/project"),
            os_name="test-os",
            current_date="2026-06-14",
            provider_name="anthropic",
            model="claude-opus-4-8",
            git_status="clean",
        )
        prompt = PromptAssembler(default_prompt_modules()).assemble(runtime_context=context)

        stable_text = "\n".join(block.text for block in prompt.stable_blocks)
        dynamic_text = "\n".join(block.text for block in prompt.dynamic_blocks)
        self.assertNotIn("/tmp/project", stable_text)
        self.assertNotIn("2026-06-14", stable_text)
        self.assertNotIn("test-os", stable_text)
        self.assertIn("project", dynamic_text)
        self.assertIn("2026-06-14", dynamic_text)
        self.assertIn("test-os", dynamic_text)
        self.assertIn("anthropic", dynamic_text)
        self.assertIn("claude-opus-4-8", dynamic_text)

    def test_dynamic_instruction_renders_system_reminder(self) -> None:
        instruction = DynamicInstruction(name="notice", content="只读。")

        self.assertEqual(instruction.render(), "<system-reminder>\n只读。\n</system-reminder>")
        block = instruction.to_block()
        self.assertEqual(block.name, "notice")
        self.assertEqual(block.stability, PromptStability.DYNAMIC)
        self.assertFalse(block.cache_policy.cacheable)

    def test_plan_only_instruction_mentions_read_only_limits(self) -> None:
        text = plan_only_instruction().render()

        self.assertIn("plan-only", text)
        self.assertIn("write_file", text)
        self.assertIn("edit_file", text)
        self.assertIn("run_command", text)

    def test_default_modules_contain_tool_usage_rules(self) -> None:
        prompt = PromptAssembler(default_prompt_modules()).assemble()
        names = [block.name for block in prompt.stable_blocks]
        text = "\n".join(block.text for block in prompt.stable_blocks)

        self.assertEqual(
            names,
            ["identity", "behavior", "tool_usage", "code_style", "safety", "task_mode", "output_style"],
        )
        self.assertIn("read_file", text)
        self.assertIn("glob_files", text)
        self.assertIn("grep_content", text)
        self.assertIn("Before editing", text)
        self.assertIn("write_file", text)
        self.assertIn("run_command", text)

    def test_blocks_property_keeps_stable_before_dynamic(self) -> None:
        context = RuntimeContext(
            workdir=Path("/tmp/project"),
            os_name="test-os",
            current_date="2026-06-14",
            provider_name="anthropic",
            model="claude-opus-4-8",
        )
        prompt = PromptAssembler(default_prompt_modules()).assemble(
            runtime_context=context,
            dynamic_instructions=[DynamicInstruction(name="notice", content="只读。")],
        )

        self.assertEqual(prompt.blocks[: len(prompt.stable_blocks)], prompt.stable_blocks)
        self.assertEqual(prompt.blocks[len(prompt.stable_blocks) :], prompt.dynamic_blocks)
        self.assertEqual(prompt.dynamic_blocks[0].name, "runtime_context")
        self.assertEqual(prompt.dynamic_blocks[1].name, "notice")

    def test_stable_blocks_are_cacheable_by_default(self) -> None:
        prompt = PromptAssembler(default_prompt_modules()).assemble()

        self.assertTrue(all(block.cache_policy.cacheable for block in prompt.stable_blocks))

    def test_dynamic_module_is_not_cacheable_by_default(self) -> None:
        prompt = PromptAssembler(
            [PromptModule("dynamic", 1, PromptStability.DYNAMIC, "runtime")]
        ).assemble()

        self.assertEqual(len(prompt.dynamic_blocks), 1)
        self.assertFalse(prompt.dynamic_blocks[0].cache_policy.cacheable)

    def test_render_text_includes_block_names(self) -> None:
        prompt = PromptAssembler([PromptModule("identity", 1, PromptStability.STABLE, "hello")]).assemble()

        self.assertEqual(prompt.render_text(), "[identity]\nhello")

    def test_explicit_cache_policy_is_respected(self) -> None:
        module = PromptModule(
            "stable-no-cache",
            1,
            PromptStability.STABLE,
            "text",
            PromptCachePolicy.none(),
        )

        block = PromptAssembler([module]).assemble().stable_blocks[0]

        self.assertFalse(block.cache_policy.cacheable)

    def test_prompt_block_render_handles_empty_text(self) -> None:
        block = PromptBlock("empty", "", PromptStability.STABLE, PromptCachePolicy.none())

        self.assertEqual(block.render(), "[empty]")


if __name__ == "__main__":
    unittest.main()
