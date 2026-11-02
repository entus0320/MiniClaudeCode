import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from miniclaudecode.hooks import HookContext, HookEngine, HookEventName, HookLoader, HookRegistry
from miniclaudecode.hooks.actions import HookActionExecutor
from miniclaudecode.hooks.core import HookAction, HookActionType, HookRule, HookSource, HookSourceKind
from miniclaudecode.hooks.matcher import get_context_value, matches_conditions
from miniclaudecode.hooks.template import render_template
from miniclaudecode.tools.base import ToolContext


class HookLoaderTests(unittest.TestCase):
    def test_project_rule_overrides_user_rule(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            user_root = root / "user"
            project_root = root / "project"
            user_root.mkdir()
            project_root.mkdir()
            (user_root / "hooks.yaml").write_text(
                """
rules:
  - name: demo
    event: message_before_send
    actions:
      - type: prompt_injection
        content: user
""".strip(),
                encoding="utf-8",
            )
            (project_root / "hooks.yaml").write_text(
                """
rules:
  - name: demo
    event: message_before_send
    actions:
      - type: prompt_injection
        content: project
""".strip(),
                encoding="utf-8",
            )

            registry = HookRegistry(HookLoader(project_root=project_root, user_root=user_root))
            registry.load_initial()

        rules = registry.list_rules()
        self.assertEqual([rule.name for rule in rules], ["demo"])
        self.assertEqual(rules[0].source.kind, HookSourceKind.PROJECT)
        self.assertIn("demo", registry.overridden)
        self.assertTrue(any(diagnostic.kind == "override" for diagnostic in registry.diagnostics))

    def test_invalid_yaml_and_invalid_rule_are_diagnostics(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            hooks_root = root / "hooks"
            hooks_root.mkdir()
            (hooks_root / "bad.yaml").write_text("rules: [", encoding="utf-8")
            (hooks_root / "invalid.yaml").write_text(
                """
rules:
  - name: broken
    event: unknown
    actions:
      - type: prompt_injection
        content: x
""".strip(),
                encoding="utf-8",
            )

            registry = HookRegistry(HookLoader(project_root=hooks_root))
            registry.load_initial()

        self.assertEqual(registry.list_rules(), [])
        rendered = "\n".join(diagnostic.render() for diagnostic in registry.diagnostics)
        self.assertIn("YAML", rendered)
        self.assertIn("unknown", rendered)
        self.assertIn("broken", rendered)

    def test_validation_errors_are_reported_with_rule_name(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            hooks_root = root / "hooks"
            hooks_root.mkdir()
            (hooks_root / "invalid.yaml").write_text(
                """
rules:
  - name: bad-shell
    event: message_before_send
    actions:
      - type: shell
  - name: bad-intercept
    event: message_before_send
    intercept:
      reason: no
    actions:
      - type: prompt_injection
        content: x
  - name: bad-regex
    event: message_before_send
    when:
      all:
        - field: message.content
          regex: "["
    actions:
      - type: prompt_injection
        content: x
""".strip(),
                encoding="utf-8",
            )

            registry = HookRegistry(HookLoader(project_root=hooks_root))
            registry.load_initial()

        rendered = "\n".join(diagnostic.render() for diagnostic in registry.diagnostics)
        self.assertIn("bad-shell", rendered)
        self.assertIn("command", rendered)
        self.assertIn("bad-intercept", rendered)
        self.assertIn("tool_before_execute", rendered)
        self.assertIn("bad-regex", rendered)
        self.assertIn("regex", rendered)


class HookMatcherAndTemplateTests(unittest.TestCase):
    def test_matchers_and_template_use_context_fields(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            hooks_root = root / "hooks"
            hooks_root.mkdir()
            (hooks_root / "hooks.yaml").write_text(
                """
rules:
  - name: demo
    event: tool_before_execute
    when:
      all:
        - field: tool.name
          exact: read_file
        - field: tool.arguments.path
          glob: "**/*.py"
        - field: message.missing
          not: forbidden
        - field: tool.arguments.path
          regex: "agent"
    actions:
      - type: prompt_injection
        content: "read ${tool.arguments.path} ${missing.value}"
""".strip(),
                encoding="utf-8",
            )
            registry = HookRegistry(HookLoader(project_root=hooks_root))
            registry.load_initial()
            rule = registry.list_rules()[0]
            context = HookContext(
                event=HookEventName.TOOL_BEFORE_EXECUTE,
                workdir=root,
                tool={"name": "read_file", "arguments": {"path": "src/agent.py"}},
            )

        self.assertTrue(matches_conditions(rule.conditions, context))
        self.assertEqual(get_context_value(context, "missing.value"), "")
        self.assertEqual(render_template("${tool.name}:${tool.arguments.path}:${missing}", context), "read_file:src/agent.py:")


class HookEngineTests(unittest.TestCase):
    def test_prompt_injection_and_once(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            hooks_root = root / "hooks"
            hooks_root.mkdir()
            (hooks_root / "hooks.yaml").write_text(
                """
rules:
  - name: once-reminder
    event: message_before_send
    once: true
    actions:
      - type: prompt_injection
        content: "remember ${message.content}"
""".strip(),
                encoding="utf-8",
            )
            registry = HookRegistry(HookLoader(project_root=hooks_root))
            registry.load_initial()
            engine = HookEngine(registry, tool_context=ToolContext(root))
            context = HookContext(
                event=HookEventName.MESSAGE_BEFORE_SEND,
                workdir=root,
                message={"content": "hello"},
            )

            engine.trigger(HookEventName.MESSAGE_BEFORE_SEND, context)
            engine.trigger(HookEventName.MESSAGE_BEFORE_SEND, context)
            instructions = engine.consume_dynamic_instructions()

        self.assertEqual(len(instructions), 1)
        self.assertIn("remember hello", instructions[0].content)

    def test_tool_before_intercept_returns_reason(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            hooks_root = root / "hooks"
            hooks_root.mkdir()
            (hooks_root / "hooks.yaml").write_text(
                """
rules:
  - name: block-read
    event: tool_before_execute
    when:
      all:
        - field: tool.name
          exact: read_file
    intercept:
      reason: "blocked ${tool.name}"
    actions:
      - type: prompt_injection
        content: "blocked"
""".strip(),
                encoding="utf-8",
            )
            registry = HookRegistry(HookLoader(project_root=hooks_root))
            registry.load_initial()
            engine = HookEngine(registry, tool_context=ToolContext(root))
            result = engine.trigger_tool_before(
                HookContext(
                    event=HookEventName.TOOL_BEFORE_EXECUTE,
                    workdir=root,
                    tool={"name": "read_file", "arguments": {}},
                )
            )

        self.assertIsNotNone(result.intercept)
        self.assertTrue(result.intercept.blocked)
        self.assertEqual(result.intercept.reason, "blocked read_file")
        self.assertEqual(result.intercept.rule_names, ("block-read",))

    def test_shell_and_http_confirmation_denial_prevents_execution(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            hooks_root = root / "hooks"
            hooks_root.mkdir()
            (hooks_root / "hooks.yaml").write_text(
                """
rules:
  - name: effects
    event: message_before_send
    actions:
      - type: shell
        command: "python --version"
      - type: http
        url: "https://example.invalid/hook"
""".strip(),
                encoding="utf-8",
            )
            calls: list[str] = []
            registry = HookRegistry(HookLoader(project_root=hooks_root))
            registry.load_initial()
            engine = HookEngine(
                registry,
                tool_context=ToolContext(root),
                confirm_action=lambda request: False,
                http_client=lambda method, url, headers, body, timeout: calls.append(url) or (200, "ok"),
            )
            result = engine.trigger(
                HookEventName.MESSAGE_BEFORE_SEND,
                HookContext(event=HookEventName.MESSAGE_BEFORE_SEND, workdir=root, message={"content": "hi"}),
            )

        self.assertEqual(calls, [])
        text = "\n".join(diagnostic.render() for diagnostic in result.diagnostics)
        self.assertIn("拒绝", text)
        self.assertIn("shell", text)
        self.assertIn("http", text)

    def test_trusted_dangerous_shell_is_still_blocked(self) -> None:
        source = HookSource(HookSourceKind.PROJECT, Path.cwd(), Path("hooks.yaml"))
        rule = HookRule(
            name="danger",
            event=HookEventName.MESSAGE_BEFORE_SEND,
            source=source,
            declaration_index=1,
            actions=(
                HookAction(
                    type=HookActionType.SHELL,
                    config={"command": "git reset --hard"},
                    trusted=True,
                ),
            ),
        )

        class StaticRegistry:
            def rules_for_event(self, event):  # type: ignore[no-untyped-def]
                return [rule]

        executor = HookActionExecutor(tool_context=ToolContext(Path.cwd()))
        result = executor.execute(rule, rule.actions[0], HookContext(event=HookEventName.MESSAGE_BEFORE_SEND), 1)

        self.assertTrue(any("git reset --hard" in diagnostic.message for diagnostic in result.diagnostics))


if __name__ == "__main__":
    unittest.main()
