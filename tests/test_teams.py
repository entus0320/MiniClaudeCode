import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from miniclaudecode.commands import CommandInvocation, CommandRegistry
from miniclaudecode.config import parse_app_config
from miniclaudecode.subagents.tools import SubAgentToolFilter, ToolFilterConfig
from miniclaudecode.teams.backends import BackendSelector
from miniclaudecode.teams.commands import register_team_commands
from miniclaudecode.teams.core import AgentTeamConfig, TeamBackendKind, TeamError, TeamMemberStatus
from miniclaudecode.teams.ids import validate_member_name, validate_team_name
from miniclaudecode.teams.in_process import InProcessBackend
from miniclaudecode.teams.lifecycle import TeamLifecycleManager
from miniclaudecode.teams.mailbox import MailboxStore
from miniclaudecode.teams.merge import ConvergeMerger, MergeSource
from miniclaudecode.teams.roster import TeamRoster
from miniclaudecode.teams.store import AgentTeamStore
from miniclaudecode.teams.tmux import TmuxPaneBackend
from miniclaudecode.teams.tools import SendMessageTool, TeamDispatchTool, TeamSpawnTool, TeamToolInjector
from miniclaudecode.tools.base import ToolContext
from miniclaudecode.tools.defaults import create_default_tool_registry
from miniclaudecode.worktrees.core import WorktreeConfig
from miniclaudecode.worktrees.manager import WorktreeManager
from miniclaudecode.worktrees.store import WorktreeStore


class FakeWorktreeGitClient:
    def __init__(self) -> None:
        self.added: list[tuple[Path, str, str | None]] = []

    def add_worktree(self, path, branch, *, base_ref=None):  # type: ignore[no-untyped-def]
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        self.added.append((path, branch, base_ref))

    def remove_worktree(self, path, *, force=False):  # type: ignore[no-untyped-def]
        return None

    def current_branch(self, cwd):  # type: ignore[no-untyped-def]
        return "miniclaudecode/team"

    def current_head(self, cwd):  # type: ignore[no-untyped-def]
        return "abc123"

    def status_porcelain(self, cwd):  # type: ignore[no-untyped-def]
        return ""

    def has_unpushed_commits(self, cwd):  # type: ignore[no-untyped-def]
        return False


class AgentTeamCoreTests(unittest.TestCase):
    def test_validate_names_reject_path_traversal(self) -> None:
        self.assertEqual(validate_team_name("demo-team"), "demo-team")
        self.assertEqual(validate_member_name("alice_01"), "alice_01")
        for value in ("", "../x", "a/b", "a\\b", "a b"):
            with self.subTest(value=value):
                with self.assertRaises(TeamError):
                    validate_team_name(value)

    def test_parse_app_config_accepts_teams_config(self) -> None:
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
                "teams": {
                    "root_dir": ".tmp/teams",
                    "backend_priority": ["tmux_pane", "in_process"],
                    "coordinator_enabled": True,
                },
            },
            environ={"ANTHROPIC_API_KEY": "secret"},
        )

        self.assertEqual(config.teams.root_dir, Path(".tmp/teams"))
        self.assertEqual(config.teams.backend_priority, ("tmux_pane", "in_process"))
        self.assertTrue(config.teams.coordinator_enabled)

    def test_store_roster_registry_and_mailbox_use_agent_id(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = AgentTeamStore(root, AgentTeamConfig(root_dir=root))
            manager = TeamLifecycleManager(store)
            manager.create_team("demo-team")
            add_result = manager.add_member("demo-team", "alice")
            team = manager.get_team("demo-team")
            member = TeamRoster().get(team, "alice")

            self.assertTrue(add_result.success)
            self.assertIsNotNone(member)
            self.assertIn(member.agent_id, str(member.mailbox_path))  # type: ignore[union-attr]
            self.assertNotIn("alice.jsonl", str(member.mailbox_path))  # type: ignore[union-attr]

            send_result = manager.send_message("demo-team", "alice", summary="hello", body="body")
            messages = MailboxStore(team.mailbox_root).read_unprocessed(member.agent_id)  # type: ignore[union-attr]

            self.assertTrue(send_result.success)
            self.assertEqual(len(messages), 1)
            self.assertEqual(messages[0].to_agent_id, member.agent_id)  # type: ignore[union-attr]


class AgentTeamToolAndBackendTests(unittest.TestCase):
    def test_team_tools_are_injected_only_for_team_member_registry(self) -> None:
        with TemporaryDirectory() as directory:
            manager = TeamLifecycleManager(AgentTeamStore(Path(directory), AgentTeamConfig(root_dir=Path(directory))))
            base = create_default_tool_registry()
            injector = TeamToolInjector(manager)
            member_registry = injector.team_member_registry(base)
            top_registry = base.copy()
            injector.register_top_level(top_registry)

            self.assertNotIn("Task", base)
            self.assertNotIn("SendMessage", base)
            self.assertIn("Task", member_registry)
            self.assertIn("SendMessage", member_registry)
            self.assertIn("TeamCreate", top_registry)
            self.assertIn("TeamDelete", top_registry)
            self.assertIn("TeamSpawn", top_registry)
            self.assertIn("TeamDispatch", top_registry)

            filtered = SubAgentToolFilter().filter(
                top_registry,
                config=ToolFilterConfig(custom_allowed=("TeamCreate", "TeamDispatch", "read_file")),
            )
            self.assertNotIn("TeamCreate", filtered.allowed_names)
            self.assertNotIn("TeamDispatch", filtered.allowed_names)

    def test_team_dispatch_writes_same_filename_in_each_member_worktree(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            worktree_manager = WorktreeManager(
                main_workdir=root,
                config=WorktreeConfig(root_dir=Path("worktrees"), state_file=Path("worktrees/state.json")),
                store=WorktreeStore(root / "worktrees" / "state.json"),
                git_client=FakeWorktreeGitClient(),
            )
            manager = TeamLifecycleManager(
                AgentTeamStore(root / "teams", AgentTeamConfig(root_dir=Path("teams"))),
                worktree_manager=worktree_manager,
            )
            manager.create_team("demo")
            manager.add_member("demo", "alice")
            manager.add_member("demo", "bob")
            tool = TeamDispatchTool(manager)

            alice = tool.execute({"team": "demo", "member": "alice", "task": "write", "path": "witness.txt", "content": "alice", "mode": "write"}, ToolContext(root))
            bob = tool.execute({"team": "demo", "member": "bob", "task": "write", "path": "witness.txt", "content": "bob", "mode": "write"}, ToolContext(root))
            team = manager.get_team("demo")
            alice_path = team.members[0].workdir / "witness.txt"
            bob_path = team.members[1].workdir / "witness.txt"

            self.assertTrue(alice.success)
            self.assertTrue(bob.success)
            self.assertEqual(alice_path.read_text(encoding="utf-8"), "alice")
            self.assertEqual(bob_path.read_text(encoding="utf-8"), "bob")
            self.assertFalse((root / "witness.txt").exists())
            self.assertNotEqual(alice_path, bob_path)

    def test_team_spawn_creates_distinct_worktrees_for_members(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            worktree_manager = WorktreeManager(
                main_workdir=root,
                config=WorktreeConfig(root_dir=Path("worktrees"), state_file=Path("worktrees/state.json")),
                store=WorktreeStore(root / "worktrees" / "state.json"),
                git_client=FakeWorktreeGitClient(),
            )
            manager = TeamLifecycleManager(
                AgentTeamStore(root / "teams", AgentTeamConfig(root_dir=Path("teams"))),
                worktree_manager=worktree_manager,
            )
            manager.create_team("demo")

            alice = manager.add_member("demo", "alice")
            bob = manager.add_member("demo", "bob")
            team = manager.get_team("demo")

            self.assertTrue(alice.success)
            self.assertTrue(bob.success)
            self.assertEqual(len(team.members), 2)
            self.assertNotEqual(team.members[0].workdir, root)
            self.assertNotEqual(team.members[1].workdir, root)
            self.assertNotEqual(team.members[0].workdir, team.members[1].workdir)
            self.assertEqual(team.members[0].metadata["worktree"], "teams/demo/alice")
            self.assertEqual(team.members[1].metadata["worktree"], "teams/demo/bob")
            self.assertIsNotNone(worktree_manager.get("teams/demo/alice"))
            self.assertIsNotNone(worktree_manager.get("teams/demo/bob"))

    def test_existing_member_spawn_updates_workdir_to_worktree(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            worktree_manager = WorktreeManager(
                main_workdir=root,
                config=WorktreeConfig(root_dir=Path("worktrees"), state_file=Path("worktrees/state.json")),
                store=WorktreeStore(root / "worktrees" / "state.json"),
                git_client=FakeWorktreeGitClient(),
            )
            manager = TeamLifecycleManager(AgentTeamStore(root / "teams", AgentTeamConfig(root_dir=Path("teams"))))
            manager.create_team("demo")
            manager.add_member("demo", "alice")
            before = manager.get_team("demo").members[0]
            manager.worktree_manager = worktree_manager

            result = manager.add_member("demo", "alice")
            after = manager.get_team("demo").members[0]

            self.assertTrue(result.success)
            self.assertEqual(before.name, after.name)
            self.assertNotEqual(before.workdir, after.workdir)
            self.assertEqual(after.metadata["worktree"], "teams/demo/alice")
            self.assertIsNotNone(worktree_manager.get("teams/demo/alice"))

    def test_team_spawn_tool_registers_member(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            manager = TeamLifecycleManager(AgentTeamStore(root, AgentTeamConfig(root_dir=root)))
            manager.create_team("demo")
            tool = TeamSpawnTool(manager)

            result = tool.execute({"team": "demo", "member": "alice"}, ToolContext(root))

            team = manager.get_team("demo")
            registry_raw = (team.registry_path).read_text(encoding="utf-8")
            self.assertTrue(result.success)
            self.assertEqual(len(team.members), 1)
            self.assertIn("alice", registry_raw)
            self.assertIn(team.members[0].agent_id, registry_raw)

    def test_send_message_tool_writes_mailbox(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            manager = TeamLifecycleManager(AgentTeamStore(root, AgentTeamConfig(root_dir=root)))
            manager.create_team("demo-team")
            manager.add_member("demo-team", "alice")
            tool = SendMessageTool(manager)

            result = tool.execute(
                {"team": "demo-team", "to": "alice", "summary": "next", "body": "continue"},
                ToolContext(root),
            )

            self.assertTrue(result.success)
            team = manager.get_team("demo-team")
            self.assertEqual(len(MailboxStore(team.mailbox_root).read_unprocessed(team.members[0].agent_id)), 1)

    def test_backend_selector_does_not_silently_fallback(self) -> None:
        selector = BackendSelector({TeamBackendKind.IN_PROCESS: InProcessBackend(), TeamBackendKind.TMUX_PANE: TmuxPaneBackend()})

        with self.assertRaises(ValueError):
            selector.select(("tmux_pane", "in_process"), allow_fallback=False)

        self.assertIs(selector.select(("tmux_pane", "in_process"), allow_fallback=True).kind, TeamBackendKind.IN_PROCESS)

    def test_tmux_wake_uses_send_keys(self) -> None:
        calls = []

        def runner(args):  # type: ignore[no-untyped-def]
            calls.append(list(args))
            return subprocess.CompletedProcess(args=list(args), returncode=0, stdout="", stderr="")

        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = AgentTeamStore(root, AgentTeamConfig(root_dir=root))
            manager = TeamLifecycleManager(store)
            manager.create_team("demo-team")
            manager.add_member("demo-team", "alice", backend=TeamBackendKind.TMUX_PANE)
            team = manager.get_team("demo-team")
            member = team.members[0]
            member = type(member)(**{**member.__dict__, "backend_ref": {"pane": "%1"}})
            message = MailboxStore(team.mailbox_root).read_unprocessed(member.agent_id)
            _ = message
            result = TmuxPaneBackend(runner=runner).wake(member, MailboxStore(team.mailbox_root).read_all(member.agent_id)[0] if MailboxStore(team.mailbox_root).read_all(member.agent_id) else __import__("miniclaudecode.teams.messages", fromlist=["make_text_message"]).make_text_message(team_id="demo-team", to_agent_id=member.agent_id, summary="s", body="b"))

        self.assertTrue(result.success)
        self.assertEqual(calls[0][:4], ["tmux", "send-keys", "-t", "%1"])


class AgentTeamCommandAndMergeTests(unittest.TestCase):
    def test_team_commands_create_member_send_and_list(self) -> None:
        with TemporaryDirectory() as directory:
            manager = TeamLifecycleManager(AgentTeamStore(Path(directory), AgentTeamConfig(root_dir=Path(directory))))
            registry = CommandRegistry()
            register_team_commands(registry, manager)
            command = registry.get("team")
            self.assertIsNotNone(command)

            create = command.handler(None, CommandInvocation(raw="/team create demo-team", name="team", args="create demo-team"))  # type: ignore[arg-type,union-attr]
            add = command.handler(None, CommandInvocation(raw="/team spawn demo-team alice", name="team", args="spawn demo-team alice"))  # type: ignore[arg-type,union-attr]
            send = command.handler(None, CommandInvocation(raw="/team send demo-team alice hello", name="team", args="send demo-team alice hello"))  # type: ignore[arg-type,union-attr]
            members = command.handler(None, CommandInvocation(raw="/team members demo-team", name="team", args="members demo-team"))  # type: ignore[arg-type,union-attr]

        self.assertIn("已创建", create.message)  # type: ignore[union-attr]
        self.assertIn("已添加", add.message)  # type: ignore[union-attr]
        self.assertIn("已投递", send.message)  # type: ignore[union-attr]
        self.assertIn("alice", members.message)  # type: ignore[union-attr]

    def test_converge_merger_applies_non_conflicting_and_stops_conflict(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            alice = root / "alice"
            bob = root / "bob"
            for path in (target, alice, bob):
                path.mkdir()
            (alice / "a.txt").write_text("alice", encoding="utf-8")
            (bob / "b.txt").write_text("bob", encoding="utf-8")
            result = ConvergeMerger(target).merge((MergeSource("alice", alice, ("a.txt",)), MergeSource("bob", bob, ("b.txt",))))

            self.assertTrue(result.success)
            self.assertEqual((target / "a.txt").read_text(encoding="utf-8"), "alice")
            self.assertEqual((target / "b.txt").read_text(encoding="utf-8"), "bob")

            (alice / "same.txt").write_text("alice", encoding="utf-8")
            (bob / "same.txt").write_text("bob", encoding="utf-8")
            conflict = ConvergeMerger(target).merge((MergeSource("alice", alice, ("same.txt",)), MergeSource("bob", bob, ("same.txt",))))

            self.assertFalse(conflict.success)
            self.assertIn("冲突", conflict.message)


if __name__ == "__main__":
    unittest.main()
