import threading
import time
import unittest
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from miniclaudecode.agent import AgentOptions, AgentRunner
from miniclaudecode.config import parse_app_config
from miniclaudecode.policy import PermissionMode, PolicyContext
from miniclaudecode.prompting import AssembledPrompt, PromptAssembler, RuntimeContext
from miniclaudecode.providers.base import BaseProvider, ProviderEvent, ProviderTextDelta, ProviderTurnComplete, ProviderUsage
from miniclaudecode.tools.base import ToolCall
from miniclaudecode.session import Message, Session
from miniclaudecode.subagents import AgentDefinitionLoader, AgentDefinitionRegistry, SubAgentSpawnRequest, SubAgentStatus
from miniclaudecode.subagents.core import AgentDefinitionSourceKind, SubAgentLaunchMode, SubAgentRunResult
from miniclaudecode.subagents.fork import ForkContextBuilder
from miniclaudecode.subagents.parser import parse_agent_frontmatter
from miniclaudecode.subagents.runtime import SubAgentRuntime, SubAgentRuntimeDependencies
from miniclaudecode.subagents.tasks import TaskManager
from miniclaudecode.subagents.tool import AgentTool, SubAgentLauncher
from miniclaudecode.subagents.tools import SubAgentToolFilter, ToolFilterConfig
from miniclaudecode.subagents.trace import TraceRegistry
from miniclaudecode.tools.base import Tool, ToolContext, ToolResult
from miniclaudecode.worktrees.adapter import SubAgentWorktreeAdapter
from miniclaudecode.worktrees.core import WorktreeConfig
from miniclaudecode.worktrees.manager import WorktreeManager
from miniclaudecode.worktrees.store import WorktreeStore
from miniclaudecode.tools.defaults import create_default_tool_registry
from miniclaudecode.tools.registry import ToolRegistry


class ScriptedProvider(BaseProvider):
    def __init__(self, turns: list[list[ProviderEvent]]) -> None:
        self.turns = turns
        self.requests: list[list[Message]] = []
        self.prompts: list[AssembledPrompt | None] = []
        self.tool_counts: list[int] = []

    def stream_chat(self, messages: Sequence[Message], prompt: AssembledPrompt | None = None) -> Iterator[str]:
        for event in self.stream_turn(messages, prompt=prompt):
            if isinstance(event, ProviderTextDelta):
                yield event.text

    def stream_turn(self, messages: Sequence[Message], tools=None, prompt=None):  # type: ignore[no-untyped-def]
        self.requests.append(list(messages))
        self.prompts.append(prompt)
        self.tool_counts.append(0 if tools is None else len(tools))
        yield from self.turns.pop(0)


class FakeWorktreeGitClient:
    def add_worktree(self, path, branch, *, base_ref=None):  # type: ignore[no-untyped-def]
        Path(path).mkdir(parents=True, exist_ok=True)

    def remove_worktree(self, path, *, force=False):  # type: ignore[no-untyped-def]
        return None

    def current_branch(self, cwd):  # type: ignore[no-untyped-def]
        return "miniclaudecode/iso"

    def current_head(self, cwd):  # type: ignore[no-untyped-def]
        return "abc123"

    def status_porcelain(self, cwd):  # type: ignore[no-untyped-def]
        return ""

    def has_unpushed_commits(self, cwd):  # type: ignore[no-untyped-def]
        return False


class DummyTool(Tool):
    description = "Dummy tool."
    input_schema = {"type": "object", "properties": {}, "additionalProperties": False}
    requires_confirmation = False

    def __init__(self, name: str) -> None:
        self.name = name

    def execute(self, arguments: Mapping[str, Any], context: ToolContext) -> ToolResult:
        return ToolResult.ok(tool_name=self.name, content="ok")


class SubAgentDefinitionTests(unittest.TestCase):
    def test_parse_frontmatter_returns_metadata_and_body(self) -> None:
        metadata, body = parse_agent_frontmatter(
            "---\nname: demo\ndescription: Demo\n---\nBody text", Path("demo.md")
        )

        self.assertEqual(metadata["name"], "demo")
        self.assertEqual(body.strip(), "Body text")

    def test_loader_project_overrides_user_and_verify_is_configurable(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            builtin = root / "builtin"
            user = root / "user"
            project = root / "project"
            for path in (builtin, user, project):
                path.mkdir()
            (builtin / "verify.md").write_text(
                "---\nname: verify\ndescription: Verify\n---\nverify body", encoding="utf-8"
            )
            (user / "demo.md").write_text(
                "---\nname: demo\ndescription: User\n---\nuser body", encoding="utf-8"
            )
            (project / "demo.md").write_text(
                "---\nname: demo\ndescription: Project\n---\nproject body", encoding="utf-8"
            )

            disabled = AgentDefinitionRegistry(
                AgentDefinitionLoader(project_root=project, user_root=user, builtin_root=builtin, include_verify=False)
            )
            disabled.load_initial()
            enabled = AgentDefinitionRegistry(
                AgentDefinitionLoader(project_root=project, user_root=user, builtin_root=builtin, include_verify=True)
            )
            enabled.load_initial()

        self.assertEqual([definition.name for definition in disabled.list_definitions()], ["demo"])
        demo = disabled.get("demo")
        self.assertEqual(demo.description, "Project")
        self.assertEqual(demo.source.kind, AgentDefinitionSourceKind.PROJECT)
        self.assertIn("demo", disabled.overridden)
        self.assertEqual([definition.name for definition in enabled.list_definitions()], ["demo", "verify"])

    def test_agent_definition_accepts_isolation_field(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            agents_root = root / "agents"
            agents_root.mkdir()
            (agents_root / "demo.md").write_text(
                "---\nname: demo\ndescription: Demo\nisolation:\n  type: worktree\n  enabled: true\n  name: iso/demo\n---\nBody",
                encoding="utf-8",
            )
            registry = AgentDefinitionRegistry(AgentDefinitionLoader(project_root=agents_root))
            registry.load_initial()

        definition = registry.get("demo")
        self.assertTrue(definition.worktree.enabled)
        self.assertEqual(definition.worktree.name, "iso/demo")

    def test_parse_app_config_accepts_subagents_config(self) -> None:
        config = parse_app_config(
            {
                "active_provider": "anthropic",
                "providers": {
                    "anthropic": {
                        "protocol": "anthropic",
                        "model": "claude-opus-4-8",
                        "credential_env": "ANTHROPIC_API_KEY",
                    }
                },
                "subagents": {
                    "include_verify_agent": True,
                    "background_allowed_tools": ["read_file"],
                },
            },
            environ={"ANTHROPIC_API_KEY": "secret"},
        )

        self.assertTrue(config.subagents.include_verify_agent)
        self.assertEqual(tuple(config.subagents.background_allowed_tools), ("read_file",))


class SubAgentToolFilterTests(unittest.TestCase):
    def test_filter_layers_only_narrow_tool_set(self) -> None:
        registry = ToolRegistry()
        for name in ("read_file", "glob_files", "grep_content", "run_command", "agent"):
            registry.register(DummyTool(name))
        definitions = AgentDefinitionRegistry(AgentDefinitionLoader())
        _ = definitions
        result = SubAgentToolFilter().filter(
            registry,
            config=ToolFilterConfig(
                background_allowed=("read_file", "agent"),
                custom_allowed=("read_file", "run_command", "agent"),
                custom_disallowed=("run_command",),
            ),
            background=True,
        )

        self.assertEqual(result.allowed_names, ("read_file",))
        self.assertIn("agent", result.removed)
        self.assertIn("run_command", result.removed)


class SubAgentRuntimeTests(unittest.TestCase):
    def test_definition_runtime_uses_independent_session_and_prompt(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            agents_root = root / "agents"
            agents_root.mkdir()
            (agents_root / "demo.md").write_text(
                "---\nname: demo\ndescription: Demo agent\nmax_rounds: 2\ntools:\n  - read_file\n---\nDEMO BODY",
                encoding="utf-8",
            )
            registry = AgentDefinitionRegistry(AgentDefinitionLoader(project_root=agents_root), tool_registry=create_default_tool_registry())
            registry.load_initial()
            parent_session = Session()
            parent_session.add_user_message("parent")
            provider = ScriptedProvider(
                [[ProviderTextDelta("child done"), ProviderTurnComplete(text="child done", usage=ProviderUsage(input_tokens=3, output_tokens=2))]]
            )
            runtime = SubAgentRuntime(
                SubAgentRuntimeDependencies(
                    provider=provider,
                    parent_session=parent_session,
                    tool_registry=create_default_tool_registry(),
                    tool_context=ToolContext(root),
                    prompt_assembler=PromptAssembler([]),
                    runtime_context_factory=lambda: RuntimeContext(root, "test", "2026-01-01", "test", "model"),
                    policy_context=PolicyContext(workdir=root, permission_mode=PermissionMode.STRICT),
                    background_allowed_tools=("read_file",),
                )
            )
            result = runtime.run_to_completion(SubAgentSpawnRequest(task="do child", subagent_type="demo"), definition=registry.get("demo"))

        self.assertTrue(result.success)
        self.assertEqual(result.final_text, "child done")
        self.assertEqual(result.usage.input_tokens, 3)
        self.assertEqual(parent_session.get_history(), [Message(role="user", content="parent")])
        self.assertEqual(provider.requests[0], [Message(role="user", content="do child")])
        self.assertIn("DEMO BODY", provider.prompts[0].render_text())
        self.assertEqual(provider.tool_counts, [1])

    def test_definition_runtime_uses_worktree_isolation(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            agents_root = root / "agents"
            agents_root.mkdir()
            (agents_root / "demo.md").write_text(
                "---\nname: demo\ndescription: Demo agent\nisolation:\n  type: worktree\n  enabled: true\n  name: iso/demo\n  temporary: true\n---\nDEMO BODY",
                encoding="utf-8",
            )
            registry = AgentDefinitionRegistry(AgentDefinitionLoader(project_root=agents_root), tool_registry=create_default_tool_registry())
            registry.load_initial()
            provider = ScriptedProvider([[ProviderTextDelta("child done"), ProviderTurnComplete(text="child done")]])
            manager = WorktreeManager(
                main_workdir=root,
                config=WorktreeConfig(root_dir=Path("worktrees"), state_file=Path("state.json")),
                store=WorktreeStore(root / "state.json"),
                git_client=FakeWorktreeGitClient(),
            )
            runtime = SubAgentRuntime(
                SubAgentRuntimeDependencies(
                    provider=provider,
                    parent_session=Session(),
                    tool_registry=create_default_tool_registry(),
                    tool_context=ToolContext(root),
                    prompt_assembler=PromptAssembler([]),
                    runtime_context_factory=lambda: RuntimeContext(root, "test", "2026-01-01", "test", "model"),
                    worktree_adapter=SubAgentWorktreeAdapter(manager),
                )
            )
            result = runtime.run_to_completion(SubAgentSpawnRequest(task="do child", subagent_type="demo"), definition=registry.get("demo"))

        self.assertTrue(result.success)
        self.assertIn("Git Worktree 隔离目录", provider.requests[0][0].content)
        self.assertIn("worktrees", str(provider.requests[0][0].content))

    def test_worktree_isolated_runtime_can_write_without_touching_parent_workdir(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "witness.txt").write_text("base", encoding="utf-8")
            agents_root = root / "agents"
            agents_root.mkdir()
            (agents_root / "demo.md").write_text(
                "---\nname: demo\ndescription: Demo agent\ntools:\n  - read_file\n  - write_file\nisolation:\n  type: worktree\n  enabled: true\n  name: iso/demo\n  temporary: false\n---\nDEMO BODY",
                encoding="utf-8",
            )
            registry = AgentDefinitionRegistry(AgentDefinitionLoader(project_root=agents_root), tool_registry=create_default_tool_registry())
            registry.load_initial()
            provider = ScriptedProvider(
                [
                    [ProviderTurnComplete(text="", tool_calls=[ToolCall(id="call-1", name="write_file", arguments={"path": "witness.txt", "content": "child"})])],
                    [ProviderTurnComplete(text="done")],
                ]
            )
            manager = WorktreeManager(
                main_workdir=root,
                config=WorktreeConfig(root_dir=Path("worktrees"), state_file=Path("state.json")),
                store=WorktreeStore(root / "state.json"),
                git_client=FakeWorktreeGitClient(),
            )
            runtime = SubAgentRuntime(
                SubAgentRuntimeDependencies(
                    provider=provider,
                    parent_session=Session(),
                    tool_registry=create_default_tool_registry(),
                    tool_context=ToolContext(root),
                    prompt_assembler=PromptAssembler([]),
                    runtime_context_factory=lambda: RuntimeContext(root, "test", "2026-01-01", "test", "model"),
                    policy_context=PolicyContext(workdir=root, permission_mode=PermissionMode.STRICT),
                    background_allowed_tools=("read_file", "glob_files", "grep_content"),
                    worktree_adapter=SubAgentWorktreeAdapter(manager),
                )
            )
            result = runtime.run_to_completion(SubAgentSpawnRequest(task="write child", subagent_type="demo", background=True), definition=registry.get("demo"))
            worktree_path = manager.get("iso/demo").path  # type: ignore[union-attr]

            self.assertTrue(result.success)
            self.assertEqual((root / "witness.txt").read_text(encoding="utf-8"), "base")
            self.assertEqual((worktree_path / "witness.txt").read_text(encoding="utf-8"), "child")

    def test_fork_context_copies_parent_history_and_appends_constraints(self) -> None:
        parent = [Message(role="user", content="hello"), Message(role="assistant", content="hi")]
        context = ForkContextBuilder().build(parent, "inspect")

        self.assertEqual(context.history[:2], parent)
        self.assertIn("不要再次启动", context.history[-1].content)
        self.assertIn("inspect", context.history[-1].content)


class SubAgentTaskAndToolTests(unittest.TestCase):
    def test_task_manager_records_completion_and_notification(self) -> None:
        trace_registry = TraceRegistry()
        manager = TaskManager(trace_registry=trace_registry)

        def run(cancel_event: threading.Event, trace_id: str | None, task_id: str) -> SubAgentRunResult:
            self.assertFalse(cancel_event.is_set())
            return SubAgentRunResult(
                success=True,
                status=SubAgentStatus.COMPLETED,
                final_text="done",
                trace_id=trace_id,
                task_id=task_id,
            )

        record = manager.start_background(
            SubAgentSpawnRequest(task="background", background=True),
            launch_mode=SubAgentLaunchMode.DEFINITION,
            run_callable=run,
            subagent_type="demo",
        )
        for _ in range(50):
            current = manager.get(record.task_id)
            if current is not None and current.status is SubAgentStatus.COMPLETED:
                break
            time.sleep(0.02)

        current = manager.get(record.task_id)
        self.assertIsNotNone(current)
        self.assertEqual(current.status, SubAgentStatus.COMPLETED)
        notifications = manager.poll_notifications()
        self.assertEqual(len(notifications), 1)
        self.assertIn(record.task_id, notifications[0].render())
        self.assertEqual(trace_registry.for_task(record.task_id).status, SubAgentStatus.COMPLETED)  # type: ignore[union-attr]

    def test_agent_tool_launches_background_fork_when_subagent_type_missing(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            parent_session = Session()
            parent_session.add_user_message("parent")
            provider = ScriptedProvider([[ProviderTurnComplete(text="fork done")]])
            trace_registry = TraceRegistry()
            task_manager = TaskManager(trace_registry=trace_registry)
            runtime = SubAgentRuntime(
                SubAgentRuntimeDependencies(
                    provider=provider,
                    parent_session=parent_session,
                    tool_registry=create_default_tool_registry(),
                    tool_context=ToolContext(root),
                    background_allowed_tools=("read_file", "glob_files", "grep_content"),
                )
            )
            launcher = SubAgentLauncher(
                registry=AgentDefinitionRegistry(AgentDefinitionLoader()),
                runtime=runtime,
                task_manager=task_manager,
            )
            tool = AgentTool(launcher)
            result = tool.execute({"task": "fork task"}, ToolContext(root))

        self.assertTrue(result.success)
        self.assertIn("task-", result.content)
        self.assertTrue(result.metadata["background"])


if __name__ == "__main__":
    unittest.main()
