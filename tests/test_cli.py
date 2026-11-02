import io
import unittest
from collections.abc import Mapping
from contextlib import redirect_stderr
from pathlib import Path
from typing import Any
from unittest.mock import patch

from miniclaudecode.config import AppConfig, ConfigError, McpServerConfig, ProviderConfig
from miniclaudecode.cli import main
from miniclaudecode.mcp.client import McpToolDescription
from miniclaudecode.providers.base import BaseProvider
from miniclaudecode.tools.base import Tool, ToolContext, ToolResult


class FakeProvider(BaseProvider):
    def stream_chat(self, messages):  # type: ignore[no-untyped-def]
        yield from ()


class FakeMcpTool(Tool):
    name = "fake__echo"
    description = "Fake MCP tool."
    input_schema = {"type": "object", "properties": {}, "additionalProperties": False}
    requires_confirmation = True

    def execute(self, arguments: Mapping[str, Any], context: ToolContext) -> ToolResult:
        return ToolResult.ok(tool_name=self.name, content="ok")


class FakeMcpConnectionManager:
    last_instance = None

    def __init__(self, configs):  # type: ignore[no-untyped-def]
        self.configs = tuple(configs)
        self.startup_warnings = ["MCP warning"] if self.configs else []
        self.closed = False
        FakeMcpConnectionManager.last_instance = self

    def discover_tools(self, registry):  # type: ignore[no-untyped-def]
        if self.configs:
            registry.register(FakeMcpTool())

    def close_all(self) -> None:
        self.closed = True


class FakeApp:
    last_provider = None
    last_session = None
    last_provider_name = None
    last_model = None
    last_tool_registry = None
    last_tool_context = None
    last_prompt_assembler = None
    last_runtime_context_factory = None
    last_policy_engine = None
    last_policy_context = None
    last_context_manager = None
    last_session_store = None
    last_memory_manager = None
    last_hook_engine = None
    last_task_manager = None
    last_trace_registry = None
    last_subagent_registry = None
    last_worktree_manager = None
    last_worktree_cleanup = None
    last_team_manager = None
    last_mcp_manager = None
    last_startup_warnings = None

    def __init__(
        self,
        provider,
        session,
        *,
        provider_name: str,
        model: str,
        tool_registry=None,
        tool_context=None,
        prompt_assembler=None,
        runtime_context_factory=None,
        policy_engine=None,
        policy_context=None,
        context_manager=None,
        session_store=None,
        memory_manager=None,
        command_registry=None,
        skill_runtime=None,
        hook_engine=None,
        task_manager=None,
        trace_registry=None,
        subagent_registry=None,
        worktree_manager=None,
        worktree_cleanup=None,
        team_manager=None,
        mcp_manager=None,
        startup_warnings=(),
    ) -> None:  # type: ignore[no-untyped-def]
        FakeApp.last_provider = provider
        FakeApp.last_session = session
        FakeApp.last_provider_name = provider_name
        FakeApp.last_model = model
        FakeApp.last_tool_registry = tool_registry
        FakeApp.last_tool_context = tool_context
        FakeApp.last_prompt_assembler = prompt_assembler
        FakeApp.last_runtime_context_factory = runtime_context_factory
        FakeApp.last_policy_engine = policy_engine
        FakeApp.last_policy_context = policy_context
        FakeApp.last_context_manager = context_manager
        FakeApp.last_session_store = session_store
        FakeApp.last_memory_manager = memory_manager
        FakeApp.last_hook_engine = hook_engine
        FakeApp.last_task_manager = task_manager
        FakeApp.last_trace_registry = trace_registry
        FakeApp.last_subagent_registry = subagent_registry
        FakeApp.last_worktree_manager = worktree_manager
        FakeApp.last_worktree_cleanup = worktree_cleanup
        FakeApp.last_team_manager = team_manager
        FakeApp.last_mcp_manager = mcp_manager
        FakeApp.last_startup_warnings = tuple(startup_warnings)

    def run(self) -> int:
        return 0


class CliTests(unittest.TestCase):
    def test_main_loads_config_creates_provider_and_runs_app(self) -> None:
        provider_config = ProviderConfig(
            name="anthropic",
            protocol="anthropic",
            model="claude-opus-4-8",
            credential_env="ANTHROPIC_API_KEY",
            credential="test-secret",
        )
        app_config = AppConfig(
            active_provider=provider_config,
            providers={"anthropic": provider_config},
        )
        provider = FakeProvider()

        with patch("miniclaudecode.cli.load_config", return_value=app_config), patch(
            "miniclaudecode.cli.create_provider", return_value=provider
        ), patch("miniclaudecode.cli.MiniClaudeCodeApp", FakeApp), patch("miniclaudecode.cli.Path.cwd", return_value=Path("/tmp/project")):
            exit_code = main([])

        self.assertEqual(exit_code, 0)
        self.assertIs(FakeApp.last_provider, provider)
        self.assertEqual(FakeApp.last_provider_name, "anthropic")
        self.assertEqual(FakeApp.last_model, "claude-opus-4-8")
        self.assertEqual(FakeApp.last_session.get_history(), [])
        self.assertIsNotNone(FakeApp.last_tool_registry)
        self.assertIsNotNone(FakeApp.last_tool_context)
        self.assertIsNotNone(FakeApp.last_prompt_assembler)
        self.assertIsNotNone(FakeApp.last_runtime_context_factory)
        self.assertIsNotNone(FakeApp.last_policy_engine)
        self.assertIsNotNone(FakeApp.last_policy_context)
        self.assertIsNotNone(FakeApp.last_context_manager)
        self.assertIsNotNone(FakeApp.last_hook_engine)
        self.assertIsNotNone(FakeApp.last_task_manager)
        self.assertIsNotNone(FakeApp.last_trace_registry)
        self.assertIsNotNone(FakeApp.last_subagent_registry)
        self.assertIsNotNone(FakeApp.last_worktree_manager)
        self.assertIsNotNone(FakeApp.last_worktree_cleanup)
        self.assertIsNotNone(FakeApp.last_team_manager)
        self.assertIn("TeamCreate", FakeApp.last_tool_registry)
        self.assertIn("TeamSpawn", FakeApp.last_tool_registry)
        self.assertIn("TeamDispatch", FakeApp.last_tool_registry)
        self.assertNotIn("Task", FakeApp.last_tool_registry)
        self.assertIn("agent", FakeApp.last_tool_registry)
        self.assertIs(FakeApp.last_session.tool_result_slimmer, FakeApp.last_context_manager.tool_result_slimmer)
        runtime_context = FakeApp.last_runtime_context_factory()
        self.assertEqual(runtime_context.provider_name, "anthropic")
        self.assertEqual(runtime_context.model, "claude-opus-4-8")
        self.assertNotIn("test-secret", runtime_context.render())

    def test_main_registers_mcp_tools_and_passes_warnings(self) -> None:
        provider_config = ProviderConfig(
            name="anthropic",
            protocol="anthropic",
            model="claude-opus-4-8",
            credential_env="ANTHROPIC_API_KEY",
            credential="test-secret",
        )
        app_config = AppConfig(
            active_provider=provider_config,
            providers={"anthropic": provider_config},
            mcp_servers=(McpServerConfig(name="fake", transport="stdio", command="python"),),
        )
        provider = FakeProvider()

        with patch("miniclaudecode.cli.load_config", return_value=app_config), patch(
            "miniclaudecode.cli.create_provider", return_value=provider
        ), patch("miniclaudecode.cli.McpConnectionManager", FakeMcpConnectionManager), patch(
            "miniclaudecode.cli.MiniClaudeCodeApp", FakeApp
        ), patch("miniclaudecode.cli.Path.cwd", return_value=Path("/tmp/project")):
            exit_code = main([])

        self.assertEqual(exit_code, 0)
        self.assertIn("fake__echo", FakeApp.last_tool_registry)
        self.assertIs(FakeApp.last_mcp_manager, FakeMcpConnectionManager.last_instance)
        self.assertEqual(FakeApp.last_startup_warnings, ("MCP warning",))

    def test_main_returns_one_for_config_error(self) -> None:
        stderr = io.StringIO()

        with patch(
            "miniclaudecode.cli.load_config",
            side_effect=ConfigError("Configuration file 'config.yaml' was not found. Copy config.example.yaml to config.yaml."),
        ), redirect_stderr(stderr):
            exit_code = main([])

        self.assertEqual(exit_code, 1)
        self.assertIn("config.yaml", stderr.getvalue())
        self.assertIn("config.example.yaml", stderr.getvalue())

    def test_main_error_output_does_not_include_secret_from_config_error(self) -> None:
        stderr = io.StringIO()

        with patch(
            "miniclaudecode.cli.load_config",
            side_effect=ConfigError("Required environment variable 'ANTHROPIC_API_KEY' is missing or empty."),
        ), redirect_stderr(stderr):
            exit_code = main([])

        self.assertEqual(exit_code, 1)
        self.assertIn("ANTHROPIC_API_KEY", stderr.getvalue())
        self.assertNotIn("test-secret", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
