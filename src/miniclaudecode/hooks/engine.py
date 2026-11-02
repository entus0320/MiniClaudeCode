"""Hook rule trigger engine."""

from __future__ import annotations

import threading
from dataclasses import replace
from collections.abc import Callable

from miniclaudecode.hooks.actions import ConfirmHookAction, HookActionExecutor, HttpClient
from miniclaudecode.hooks.core import (
    HookContext,
    HookDiagnostic,
    HookEventName,
    HookInterceptResult,
    HookRule,
    HookTriggerResult,
)
from miniclaudecode.hooks.matcher import matches_conditions
from miniclaudecode.hooks.registry import HookRegistry
from miniclaudecode.hooks.template import render_template
from miniclaudecode.prompting import DynamicInstruction
from miniclaudecode.tools.base import ToolContext

HookDiagnosticCallback = Callable[[HookDiagnostic], None]


class HookEngine:
    """Trigger loaded Hook rules and isolate their failures."""

    def __init__(
        self,
        registry: HookRegistry,
        *,
        tool_context: ToolContext,
        confirm_action: ConfirmHookAction | None = None,
        http_client: HttpClient | None = None,
        diagnostic_callback: HookDiagnosticCallback | None = None,
    ) -> None:
        self.registry = registry
        self.tool_context = tool_context
        self.diagnostic_callback = diagnostic_callback
        self.executor = HookActionExecutor(
            tool_context=tool_context,
            confirm_action=confirm_action,
            http_client=http_client,
        )
        self._once_keys: set[tuple[str, str]] = set()
        self._pending_instructions: list[DynamicInstruction] = []
        self._lock = threading.Lock()

    def set_confirm_action(self, confirm_action: ConfirmHookAction | None) -> None:
        """Update the confirmation callback used by side-effecting actions."""

        self.executor.confirm_action = confirm_action

    def set_diagnostic_callback(self, callback: HookDiagnosticCallback | None) -> None:
        """Update callback for asynchronous diagnostics."""

        self.diagnostic_callback = callback

    def reset_session_state(self) -> None:
        """Clear session-scoped once state and pending prompt injections."""

        with self._lock:
            self._once_keys.clear()
            self._pending_instructions.clear()

    def consume_dynamic_instructions(self) -> tuple[DynamicInstruction, ...]:
        """Return and clear pending prompt injections."""

        with self._lock:
            instructions = tuple(self._pending_instructions)
            self._pending_instructions.clear()
        return instructions

    def trigger(self, event: HookEventName, context: HookContext | None = None) -> HookTriggerResult:
        """Trigger all matching rules for an event."""

        hook_context = context or HookContext(event=event, workdir=self.tool_context.workdir)
        if hook_context.event is not event:
            hook_context = replace(hook_context, event=event)
        return self._trigger(event, hook_context)

    def trigger_tool_before(self, context: HookContext) -> HookTriggerResult:
        """Trigger tool-before rules and return an optional intercept result."""

        return self._trigger(HookEventName.TOOL_BEFORE_EXECUTE, replace(context, event=HookEventName.TOOL_BEFORE_EXECUTE))

    def _trigger(self, event: HookEventName, context: HookContext) -> HookTriggerResult:
        diagnostics: list[HookDiagnostic] = []
        dynamic_instructions: list[DynamicInstruction] = []
        intercept_reasons: list[str] = []
        intercept_rules: list[str] = []

        for rule in self.registry.rules_for_event(event):
            try:
                if not matches_conditions(rule.conditions, context):
                    continue
                if rule.once and self._has_run_once(rule):
                    continue
                if rule.once:
                    self._mark_once(rule)

                action_context = context
                if rule.intercept_reason is not None:
                    reason = render_template(rule.intercept_reason, context)
                    intercept_reasons.append(reason)
                    intercept_rules.append(rule.name)
                    action_context = replace(
                        context,
                        extra={**context.extra, "intercept": {"reason": reason}},
                    )

                for index, action in enumerate(rule.actions, start=1):
                    if action.async_execute:
                        self._start_async_action(rule, action_context, index)
                        continue
                    action_result = self.executor.execute(rule, action, action_context, index)
                    diagnostics.extend(action_result.diagnostics)
                    dynamic_instructions.extend(action_result.dynamic_instructions)
            except Exception as exc:
                diagnostics.append(
                    HookDiagnostic(
                        f"Hook 规则执行失败：{exc}",
                        kind="execution_error",
                        rule_name=rule.name,
                        event=rule.event,
                        source_path=rule.source.path,
                    )
                )

        if dynamic_instructions:
            with self._lock:
                self._pending_instructions.extend(dynamic_instructions)

        intercept = None
        if intercept_reasons:
            intercept = HookInterceptResult(
                blocked=True,
                reason="；".join(reason for reason in intercept_reasons if reason),
                rule_names=tuple(intercept_rules),
            )
        return HookTriggerResult(
            diagnostics=tuple(diagnostics),
            dynamic_instructions=tuple(dynamic_instructions),
            intercept=intercept,
        )

    def _start_async_action(self, rule: HookRule, context: HookContext, action_index: int) -> None:
        action = rule.actions[action_index - 1]

        def run() -> None:
            result = self.executor.execute(rule, action, context, action_index)
            if self.diagnostic_callback is not None:
                for diagnostic in result.diagnostics:
                    self.diagnostic_callback(diagnostic)
            if result.dynamic_instructions:
                with self._lock:
                    self._pending_instructions.extend(result.dynamic_instructions)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()

    def _has_run_once(self, rule: HookRule) -> bool:
        key = (rule.name, str(rule.source.path))
        with self._lock:
            return key in self._once_keys

    def _mark_once(self, rule: HookRule) -> None:
        key = (rule.name, str(rule.source.path))
        with self._lock:
            self._once_keys.add(key)
