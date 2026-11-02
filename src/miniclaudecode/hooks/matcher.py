"""Hook condition matching."""

from __future__ import annotations

import fnmatch
import re
from typing import Any

from miniclaudecode.hooks.core import HookCondition, HookConditionGroup, HookConditionOperator, HookContext


def matches_conditions(group: HookConditionGroup | None, context: HookContext) -> bool:
    """Return whether a Hook condition group matches a context."""

    if group is None:
        return True
    checks = [_matches_condition(condition, context) for condition in group.conditions]
    if group.mode == "any":
        return any(checks)
    return all(checks)


def get_context_value(context: HookContext, field: str) -> str:
    """Read a dotted field from HookContext, returning empty string when missing."""

    value: Any = context.to_mapping()
    for part in field.split("."):
        if isinstance(value, dict):
            value = value.get(part, "")
        else:
            value = getattr(value, part, "")
        if value is None:
            return ""
    if isinstance(value, str):
        return value
    if isinstance(value, int | float | bool):
        return str(value)
    return str(value) if value else ""


def _matches_condition(condition: HookCondition, context: HookContext) -> bool:
    actual = get_context_value(context, condition.field)
    expected = condition.value
    if condition.operator is HookConditionOperator.EXACT:
        return actual == expected
    if condition.operator is HookConditionOperator.NOT:
        return actual != expected
    if condition.operator is HookConditionOperator.REGEX:
        return re.search(expected, actual) is not None
    if condition.operator is HookConditionOperator.GLOB:
        return fnmatch.fnmatch(actual, expected)
    return False
