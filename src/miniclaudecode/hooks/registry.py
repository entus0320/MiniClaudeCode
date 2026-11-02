"""Runtime registry for loaded Hook rules."""

from __future__ import annotations

from miniclaudecode.hooks.core import HookDiagnostic, HookEventName, HookReloadResult, HookRule
from miniclaudecode.hooks.loader import HookLoader


class HookRegistry:
    """Hold loaded Hook rules and support safe reloads."""

    def __init__(self, loader: HookLoader) -> None:
        self.loader = loader
        self._rules: tuple[HookRule, ...] = ()
        self._diagnostics: tuple[HookDiagnostic, ...] = ()
        self._overridden: tuple[str, ...] = ()

    def load_initial(self) -> None:
        result = self.loader.discover()
        self._replace_state(result.rules, result.diagnostics, result.overridden)

    def reload(self) -> HookReloadResult:
        old_names = {rule.name for rule in self._rules}
        try:
            result = self.loader.discover()
        except Exception as exc:
            return HookReloadResult(diagnostics=self._diagnostics, error=str(exc))
        new_names = {rule.name for rule in result.rules}
        self._replace_state(result.rules, result.diagnostics, result.overridden)
        return HookReloadResult(
            added=tuple(sorted(new_names - old_names)),
            removed=tuple(sorted(old_names - new_names)),
            overridden=result.overridden,
            diagnostics=result.diagnostics,
        )

    def list_rules(self) -> list[HookRule]:
        return list(self._rules)

    def rules_for_event(self, event: HookEventName) -> list[HookRule]:
        return [rule for rule in self._rules if rule.event is event]

    @property
    def diagnostics(self) -> tuple[HookDiagnostic, ...]:
        return self._diagnostics

    @property
    def overridden(self) -> tuple[str, ...]:
        return self._overridden

    def _replace_state(
        self,
        rules: tuple[HookRule, ...],
        diagnostics: tuple[HookDiagnostic, ...],
        overridden: tuple[str, ...],
    ) -> None:
        self._rules = tuple(rules)
        self._diagnostics = tuple(diagnostics)
        self._overridden = tuple(overridden)
