import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from miniclaudecode.config import parse_app_config
from miniclaudecode.worktrees import WorktreeConfig, WorktreeStore, WorktreeStatus
from miniclaudecode.worktrees.commands import register_worktree_commands
from miniclaudecode.worktrees.core import IgnoredFileRule, WorktreeError
from miniclaudecode.worktrees.environment import EnvironmentInitializer
from miniclaudecode.worktrees.manager import WorktreeManager
from miniclaudecode.worktrees.names import branch_name_for_worktree, validate_worktree_name, validate_worktree_slug
from miniclaudecode.worktrees.paths import WorktreePathResolver
from miniclaudecode.commands import CommandInvocation, CommandRegistry


class FakeGitClient:
    def __init__(self) -> None:
        self.added: list[tuple[Path, str, str | None]] = []
        self.removed: list[tuple[Path, bool]] = []
        self.dirty = False
        self.unpushed = False

    def add_worktree(self, path, branch, *, base_ref=None):  # type: ignore[no-untyped-def]
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        self.added.append((path, branch, base_ref))

    def remove_worktree(self, path, *, force=False):  # type: ignore[no-untyped-def]
        self.removed.append((Path(path), force))

    def current_branch(self, cwd):  # type: ignore[no-untyped-def]
        return "miniclaudecode/demo"

    def current_head(self, cwd):  # type: ignore[no-untyped-def]
        return "abc123"

    def status_porcelain(self, cwd):  # type: ignore[no-untyped-def]
        return " M file.py\n" if self.dirty else ""

    def has_unpushed_commits(self, cwd):  # type: ignore[no-untyped-def]
        return self.unpushed


class WorktreeSafetyTests(unittest.TestCase):
    def test_name_validation_accepts_safe_nested_name(self) -> None:
        self.assertEqual(validate_worktree_name("feature/demo_01-a"), "feature/demo_01-a")
        self.assertEqual(validate_worktree_slug("feature/demo_01-a"), "feature/demo_01-a")
        self.assertEqual(branch_name_for_worktree("feature/demo"), "miniclaudecode/feature-demo")

    def test_name_validation_rejects_path_traversal(self) -> None:
        for name in ("", "../x", "a//b", "a/./b", "a/../b", "C:/tmp", "a\\b", "a b"):
            with self.subTest(name=name):
                with self.assertRaises(WorktreeError):
                    validate_worktree_name(name)

    def test_path_resolver_keeps_path_under_root(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            resolver = WorktreePathResolver(root, WorktreeConfig(root_dir=Path(".mini/worktrees")))
            path = resolver.resolve("feature/demo")

        self.assertTrue(str(path).endswith(".mini/worktrees/feature/demo") or str(path).endswith(".mini\\worktrees\\feature\\demo"))


class WorktreeConfigTests(unittest.TestCase):
    def test_parse_app_config_accepts_worktrees_config(self) -> None:
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
                "worktrees": {
                    "root_dir": ".tmp/worktrees",
                    "branch_prefix": "agent",
                    "copy_files": ["settings.local.json"],
                    "ignored_file_rules": [{"path": "local.db", "required": True}],
                },
            },
            environ={"ANTHROPIC_API_KEY": "secret"},
        )

        self.assertEqual(config.worktrees.root_dir, Path(".tmp/worktrees"))
        self.assertEqual(config.worktrees.branch_prefix, "agent")
        self.assertEqual(config.worktrees.ignored_file_rules, (IgnoredFileRule("local.db", True),))


class WorktreeStoreAndManagerTests(unittest.TestCase):
    def test_store_marks_missing_and_unrecoverable_on_resume(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = WorktreeStore(root / "state.json")
            manager = WorktreeManager(
                main_workdir=root,
                config=WorktreeConfig(root_dir=Path("worktrees"), state_file=Path("state.json")),
                store=store,
                git_client=FakeGitClient(),
            )
            manager.create("demo")
            state = store.load()
            missing_path = state.records[0].path
            missing_path.rmdir()

            loaded = store.load(mark_unrecoverable_tasks=True)

        self.assertEqual(loaded.records[0].status, WorktreeStatus.MISSING)
        self.assertIn("missing", " ".join(loaded.warnings))

    def test_manager_create_enter_exit_and_delete_with_confirmation(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            fake_git = FakeGitClient()
            manager = WorktreeManager(
                main_workdir=root,
                config=WorktreeConfig(root_dir=Path("worktrees"), state_file=Path("state.json")),
                store=WorktreeStore(root / "state.json"),
                git_client=fake_git,
            )
            created = manager.create("demo")
            entered = manager.enter("demo")
            exited = manager.exit()
            fake_git.dirty = True
            denied = manager.delete("demo")
            deleted = manager.delete("demo", confirm_discard=True)

        self.assertTrue(created.success)
        self.assertTrue(entered.success)
        self.assertTrue(exited.success)
        self.assertTrue(denied.requires_confirmation)
        self.assertTrue(deleted.success)
        self.assertEqual(fake_git.removed[-1][1], True)

    def test_manager_auto_cleanup_and_stale_cleanup(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            fake_git = FakeGitClient()
            manager = WorktreeManager(
                main_workdir=root,
                config=WorktreeConfig(root_dir=Path("worktrees"), state_file=Path("state.json"), temporary_ttl_seconds=1),
                store=WorktreeStore(root / "state.json"),
                git_client=fake_git,
            )
            manager.create("temp", temporary=True)
            stale = manager.create("stale", temporary=False).record
            if stale is not None:
                stale.path.rmdir()
            stale_diagnostics = manager.stale_cleanup()
            auto_diagnostics = manager.auto_cleanup(now=9999999999)

        self.assertTrue(any("StaleCleanup" in diagnostic.message for diagnostic in stale_diagnostics))
        self.assertTrue(any("AutoCleanup" in diagnostic.message for diagnostic in auto_diagnostics))

    def test_worktree_commands_list_status_create(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            manager = WorktreeManager(
                main_workdir=root,
                config=WorktreeConfig(root_dir=Path("worktrees"), state_file=Path("state.json")),
                store=WorktreeStore(root / "state.json"),
                git_client=FakeGitClient(),
            )
            registry = CommandRegistry()
            register_worktree_commands(registry, manager)
            command = registry.get("worktree")
            self.assertIsNotNone(command)
            create_result = command.handler(None, CommandInvocation(raw="/worktree create demo", name="worktree", args="create demo"))  # type: ignore[arg-type,union-attr]
            list_result = command.handler(None, CommandInvocation(raw="/worktree list", name="worktree", args="list"))  # type: ignore[arg-type,union-attr]
            remove_result = command.handler(None, CommandInvocation(raw="/worktree remove demo", name="worktree", args="remove demo --confirm-discard"))  # type: ignore[arg-type,union-attr]

        self.assertIn("已创建", create_result.message)  # type: ignore[union-attr]
        self.assertIn("demo", list_result.message)  # type: ignore[union-attr]
        self.assertIn("已删除", remove_result.message)  # type: ignore[union-attr]


class EnvironmentInitializerTests(unittest.TestCase):
    def test_environment_initializer_skips_sensitive_files_and_copies_allowed(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "worktree"
            target.mkdir()
            (root / "settings.local.json").write_text("{}", encoding="utf-8")
            (root / ".env").write_text("secret", encoding="utf-8")
            initializer = EnvironmentInitializer(root, WorktreeConfig(copy_files=("settings.local.json", ".env")))

            diagnostics = initializer.initialize(target)

            self.assertTrue((target / "settings.local.json").exists())
            self.assertFalse((target / ".env").exists())
            self.assertTrue(any("不安全" in diagnostic.message for diagnostic in diagnostics))


if __name__ == "__main__":
    unittest.main()
