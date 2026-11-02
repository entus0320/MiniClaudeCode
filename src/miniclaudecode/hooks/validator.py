"""Validation helpers for Hook rules."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from miniclaudecode.hooks.core import (
    HookAction,
    HookActionType,
    HookCondition,
    HookConditionGroup,
    HookConditionOperator,
    HookEventName,
    HookRule,
    HookSource,
    HookValidationError,
)


def normalize_hook_rule(raw: Mapping[str, Any], *, source: HookSource, declaration_index: int) -> HookRule:
    """Normalize one raw YAML rule into a validated HookRule."""

    name = _required_string(raw, "name", source.path, declaration_index).strip().lower()
    _validate_name(name, source.path, declaration_index)
    event = _parse_event(_required_string(raw, "event", source.path, declaration_index), source.path, name)
    conditions = _parse_conditions(raw.get("when"), source.path, name)
    once = _optional_bool(raw.get("once"), "once", source.path, name, default=False)
    rule_async = _optional_bool(raw.get("async"), "async", source.path, name, default=False)
    intercept_reason = _parse_intercept(raw.get("intercept"), event, source.path, name)
    actions = _parse_actions(raw.get("actions"), rule_async=rule_async, source_path=source.path, rule_name=name)
    if intercept_reason is not None and any(action.async_execute for action in actions):
        raise HookValidationError(f"Hook 规则 `{name}` 的工具拦截不能异步执行：{source.path}")
    return HookRule(
        name=name,
        event=event,
        conditions=conditions,
        actions=actions,
        source=source,
        declaration_index=declaration_index,
        intercept_reason=intercept_reason,
        once=once,
    )


def _validate_name(name: str, path: Path, index: int) -> None:
    if not name:
        raise HookValidationError(f"Hook 规则名称不能为空：{path}#{index}")
    if any(char.isspace() for char in name):
        raise HookValidationError(f"Hook 规则名称不能包含空白字符：{name} | {path}#{index}")
    if "/" in name or "\\" in name:
        raise HookValidationError(f"Hook 规则名称不能包含路径分隔符：{name} | {path}#{index}")


def _parse_event(value: str, path: Path, rule_name: str) -> HookEventName:
    try:
        return HookEventName(value.strip())
    except ValueError as exc:
        raise HookValidationError(f"未知 Hook event：{value} | Rule: {rule_name} | Path: {path}") from exc


def _parse_conditions(value: Any, path: Path, rule_name: str) -> HookConditionGroup | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise HookValidationError(f"Hook when 必须是对象：{rule_name} | {path}")
    has_all = "all" in value
    has_any = "any" in value
    if has_all and has_any:
        raise HookValidationError(f"Hook when 不能同时声明 all 和 any：{rule_name} | {path}")
    if not has_all and not has_any:
        raise HookValidationError(f"Hook when 必须声明 all 或 any：{rule_name} | {path}")
    mode = "all" if has_all else "any"
    raw_conditions = value.get(mode)
    if not isinstance(raw_conditions, Sequence) or isinstance(raw_conditions, str):
        raise HookValidationError(f"Hook when.{mode} 必须是数组：{rule_name} | {path}")
    conditions = tuple(_parse_condition(item, path, rule_name, mode, index) for index, item in enumerate(raw_conditions, start=1))
    return HookConditionGroup(mode=mode, conditions=conditions)


def _parse_condition(value: Any, path: Path, rule_name: str, mode: str, index: int) -> HookCondition:
    if not isinstance(value, Mapping):
        raise HookValidationError(f"Hook 条件必须是对象：{rule_name} | {path} | when.{mode}[{index}]")
    field = _required_string(value, "field", path, index, rule_name=rule_name, field_path=f"when.{mode}[{index}].field")
    operators = [operator for operator in HookConditionOperator if operator.value in value]
    if len(operators) != 1:
        raise HookValidationError(f"Hook 条件必须且只能声明一种操作符：{rule_name} | {path} | when.{mode}[{index}]")
    operator = operators[0]
    raw_expected = value.get(operator.value)
    if not isinstance(raw_expected, str):
        raise HookValidationError(f"Hook 条件值必须是字符串：{rule_name} | {path} | when.{mode}[{index}].{operator.value}")
    if operator is HookConditionOperator.REGEX:
        try:
            re.compile(raw_expected)
        except re.error as exc:
            raise HookValidationError(f"Hook regex 非法：{rule_name} | {path} | {exc}") from exc
    return HookCondition(field=field.strip(), operator=operator, value=raw_expected)


def _parse_intercept(value: Any, event: HookEventName, path: Path, rule_name: str) -> str | None:
    if value is None:
        return None
    if event is not HookEventName.TOOL_BEFORE_EXECUTE:
        raise HookValidationError(f"Hook intercept 只能用于 tool_before_execute：{rule_name} | {path}")
    if isinstance(value, bool):
        if value:
            return "Hook 规则拦截了该工具调用。"
        return None
    if not isinstance(value, Mapping):
        raise HookValidationError(f"Hook intercept 必须是对象或 true：{rule_name} | {path}")
    reason = value.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise HookValidationError(f"Hook intercept.reason 必须是非空字符串：{rule_name} | {path}")
    return reason.strip()


def _parse_actions(value: Any, *, rule_async: bool, source_path: Path, rule_name: str) -> tuple[HookAction, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str) or not value:
        raise HookValidationError(f"Hook actions 必须是非空数组：{rule_name} | {source_path}")
    return tuple(_parse_action(item, rule_async=rule_async, source_path=source_path, rule_name=rule_name, index=index) for index, item in enumerate(value, start=1))


def _parse_action(value: Any, *, rule_async: bool, source_path: Path, rule_name: str, index: int) -> HookAction:
    if not isinstance(value, Mapping):
        raise HookValidationError(f"Hook action 必须是对象：{rule_name} | {source_path} | actions[{index}]")
    raw_type = _required_string(value, "type", source_path, index, rule_name=rule_name, field_path=f"actions[{index}].type")
    try:
        action_type = HookActionType(raw_type.strip())
    except ValueError as exc:
        raise HookValidationError(f"未知 Hook action：{raw_type} | Rule: {rule_name} | Path: {source_path}") from exc
    async_execute = _optional_bool(value.get("async"), "async", source_path, rule_name, default=rule_async)
    trusted = _optional_bool(value.get("trusted"), "trusted", source_path, rule_name, default=False)
    timeout_seconds = _optional_positive_number(value.get("timeout_seconds"), "timeout_seconds", source_path, rule_name)
    config = {str(key): item for key, item in value.items() if key not in {"type", "async", "trusted", "timeout_seconds"}}
    if action_type is HookActionType.SHELL:
        _require_config_string(config, "command", source_path, rule_name, index)
    elif action_type is HookActionType.PROMPT_INJECTION:
        _require_config_string(config, "content", source_path, rule_name, index)
    elif action_type is HookActionType.HTTP:
        _require_config_string(config, "url", source_path, rule_name, index)
        method = config.get("method", "GET")
        if not isinstance(method, str) or not method.strip():
            raise HookValidationError(f"Hook http.method 必须是非空字符串：{rule_name} | {source_path} | actions[{index}]")
        config["method"] = method.strip().upper()
    elif action_type is HookActionType.SUB_AGENT:
        _require_config_string(config, "description", source_path, rule_name, index)
    return HookAction(
        type=action_type,
        config=config,
        timeout_seconds=timeout_seconds,
        trusted=trusted,
        async_execute=async_execute,
    )


def _required_string(
    raw: Mapping[str, Any],
    key: str,
    path: Path,
    index: int,
    *,
    rule_name: str | None = None,
    field_path: str | None = None,
) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        label = rule_name or f"#{index}"
        field = field_path or key
        raise HookValidationError(f"Hook 规则缺少非空 `{field}`：{label} | {path}")
    return value


def _require_config_string(config: Mapping[str, Any], key: str, path: Path, rule_name: str, index: int) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise HookValidationError(f"Hook action 缺少非空 `{key}`：{rule_name} | {path} | actions[{index}]")
    return value


def _optional_bool(value: Any, key: str, path: Path, rule_name: str, *, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise HookValidationError(f"Hook `{key}` 必须是布尔值：{rule_name} | {path}")
    return value


def _optional_positive_number(value: Any, key: str, path: Path, rule_name: str) -> float | None:
    if value is None:
        return None
    if not isinstance(value, int | float) or value <= 0:
        raise HookValidationError(f"Hook `{key}` 必须是正数：{rule_name} | {path}")
    return float(value)
