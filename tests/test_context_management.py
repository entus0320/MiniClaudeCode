import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from miniclaudecode.context import ContextArtifactStore, ContextManagementConfig, ToolResultSlimmer
from miniclaudecode.session import Session
from miniclaudecode.tools.base import ToolResult


class ContextManagementTests(unittest.TestCase):
    def test_single_large_tool_result_is_offloaded_to_artifact(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = ContextManagementConfig(
                artifact_dir=Path(".mini/context"),
                single_tool_result_token_limit=5,
                tool_result_batch_token_limit=1000,
                tool_result_preview_chars=12,
            )
            slimmer = ToolResultSlimmer(
                config,
                ContextArtifactStore(root / config.artifact_dir, relative_root=config.artifact_dir),
            )
            session = Session(tool_result_slimmer=slimmer)
            result = ToolResult.ok(tool_name="read_file", content="abcdef" * 2000, tool_call_id="call-1")

            message = session.add_tool_result(result)

            payload = json.loads(message.content)
            self.assertEqual(message.tool_call_id, "call-1")
            self.assertEqual(message.tool_name, "read_file")
            self.assertIsNotNone(message.tool_result_offload)
            offload = message.tool_result_offload
            assert offload is not None
            self.assertEqual(offload.tool_name, "read_file")
            self.assertEqual(offload.tool_call_id, "call-1")
            self.assertEqual(offload.reason, "single_tool_result_limit")
            self.assertTrue(offload.path.startswith(".mini/context/tool-results/"))
            self.assertGreater(offload.released_chars, 0)
            self.assertGreater(offload.released_estimated_tokens, 0)
            self.assertIn("abcdefabcdef", offload.content_preview)
            self.assertIn("abcdefabcdef", payload["content_preview"])
            self.assertEqual(payload["offloaded"]["reason"], "single_tool_result_limit")
            self.assertTrue(payload["offloaded"]["path"].startswith(".mini/context/tool-results/"))
            self.assertNotIn(str(root), payload["offloaded"]["path"])
            artifact = root / payload["offloaded"]["path"]
            artifact_payload = json.loads(artifact.read_text(encoding="utf-8"))
            self.assertEqual(artifact_payload["tool_name"], "read_file")
            self.assertEqual(artifact_payload["tool_call_id"], "call-1")
            self.assertIn("abcdef" * 20, artifact_payload["content"])

    def test_batch_limit_offloads_largest_result_first_and_keeps_order(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = ContextManagementConfig(
                artifact_dir=Path(".mini/context"),
                single_tool_result_token_limit=1000,
                tool_result_batch_token_limit=45,
                tool_result_preview_chars=10,
            )
            slimmer = ToolResultSlimmer(
                config,
                ContextArtifactStore(root / config.artifact_dir, relative_root=config.artifact_dir),
            )
            session = Session(tool_result_slimmer=slimmer)
            results = [
                ToolResult.ok(tool_name="small", content="s" * 10, tool_call_id="call-small"),
                ToolResult.ok(tool_name="large", content="l" * 180, tool_call_id="call-large"),
                ToolResult.ok(tool_name="medium", content="m" * 20, tool_call_id="call-medium"),
            ]

            messages = session.add_tool_results(results)

            self.assertEqual([message.tool_call_id for message in messages], ["call-small", "call-large", "call-medium"])
            small_payload = json.loads(messages[0].content)
            large_payload = json.loads(messages[1].content)
            medium_payload = json.loads(messages[2].content)
            self.assertNotIn("offloaded", small_payload)
            self.assertIsNone(messages[0].tool_result_offload)
            self.assertEqual(large_payload["offloaded"]["reason"], "tool_result_batch_limit")
            self.assertIsNotNone(messages[1].tool_result_offload)
            self.assertNotIn("offloaded", medium_payload)
            self.assertIsNone(messages[2].tool_result_offload)

    def test_offloaded_tool_result_metadata_is_previewed_in_stub(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = ContextManagementConfig(
                artifact_dir=Path(".mini/context"),
                single_tool_result_token_limit=5,
                tool_result_preview_chars=12,
            )
            session = Session(
                tool_result_slimmer=ToolResultSlimmer(
                    config,
                    ContextArtifactStore(root / config.artifact_dir, relative_root=config.artifact_dir),
                )
            )
            stdout = "x" * 10000
            result = ToolResult.ok(
                tool_name="run_command",
                content="命令执行完成。",
                metadata={"stdout": stdout, "return_code": 0},
                tool_call_id="call-command",
            )

            message = session.add_tool_result(result)

            self.assertNotIn(stdout, message.content)
            payload = json.loads(message.content)
            self.assertEqual(payload["metadata"]["return_code"], 0)
            self.assertIn("stdout_preview", payload["metadata"])
            self.assertTrue(payload["metadata"]["metadata_offloaded"])
            artifact = root / payload["offloaded"]["path"]
            artifact_payload = json.loads(artifact.read_text(encoding="utf-8"))
            self.assertEqual(artifact_payload["metadata"]["stdout"], stdout)

    def test_user_messages_are_not_slimmed(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = ContextManagementConfig(
                artifact_dir=Path(".mini/context"),
                single_tool_result_token_limit=1,
            )
            session = Session(
                tool_result_slimmer=ToolResultSlimmer(
                    config,
                    ContextArtifactStore(root / config.artifact_dir, relative_root=config.artifact_dir),
                )
            )
            text = "用户原文" * 100

            message = session.add_user_message(text)

            self.assertEqual(message.content, text)
            self.assertEqual(list((root / config.artifact_dir).glob("**/*")), [])


if __name__ == "__main__":
    unittest.main()
