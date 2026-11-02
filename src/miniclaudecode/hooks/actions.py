"""Hook action executors."""

from __future__ import annotations

import os
import subprocess
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from miniclaudecode.hooks.core import (
    HookAction,
    HookActionType,
    HookConfirmationRequest,
    HookContext,
    HookDiagnostic,
    HookEventName,
    HookRule,
)
from miniclaudecode.hooks.template import render_template
from miniclaudecode.policy import detect_dangerous_command
from miniclaudecode.prompting import DynamicInstruction
from miniclaudecode.tools.base import ToolContext, clamp_text, sanitize_text

HttpClient = Callable[[str, str, Mapping[str, str], str | None, float], tuple[int, str]]
ConfirmHookAction = Callable[[HookConfirmationRequest], bool]

_ALLOWED_ENV_KEYS = {"PATH", "SystemRoot", "WINDIR", "ComSpec", "TEMP", "TMP", "HOME", "USERPROFILE"}
_SECRET_ENV_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")


@dataclass(frozen=True)
class HookActionResult:
    """Result from executing one Hook action."""

    diagnostics: tuple[HookDiagnostic, ...] = ()
    dynamic_instructions: tuple[DynamicInstruction, ...] = ()


class HookActionExecutor:
    """Execute Hook actions with confirmation and error isolation."""

    def __init__(
        self,
        *,
        tool_context: ToolContext,
        confirm_action: ConfirmHookAction | None = None,
        http_client: HttpClient | None = None,
    ) -> None:
        self.tool_context = tool_context
        self.confirm_action = confirm_action
        self.http_client = http_client or _default_http_client

    def execute(self, rule: HookRule, action: HookAction, context: HookContext, action_index: int) -> HookActionResult:
        """Execute one action and convert failures into diagnostics."""

        try:
            if action.type is HookActionType.SHELL:
                return self._execute_shell(rule, action, context)
            if action.type is HookActionType.PROMPT_INJECTION:
                return self._execute_prompt_injection(rule, action, context, action_index)
            if action.type is HookActionType.HTTP:
                return self._execute_http(rule, action, context)
            if action.type is HookActionType.SUB_AGENT:
                return HookActionResult(
                    diagnostics=(
                        HookDiagnostic(
                            "子 Agent 动作当前仅预留，未实现真实子 Agent 调度。",
                            kind="action",
                            rule_name=rule.name,
                            event=rule.event,
                            source_path=rule.source.path,
                            action_type=action.type,
                        ),
                    )
                )
        except Exception as exc:
            return HookActionResult(
                diagnostics=(
                    HookDiagnostic(
                        f"Hook 动作执行失败：{exc}",
                        kind="action_error",
                        rule_name=rule.name,
                        event=rule.event,
                        source_path=rule.source.path,
                        action_type=action.type,
                    ),
                )
            )
        return HookActionResult()

    def _execute_shell(self, rule: HookRule, action: HookAction, context: HookContext) -> HookActionResult:
        command = render_template(str(action.config["command"]), context)
        hazard = detect_dangerous_command(command)
        if hazard is not None:
            return HookActionResult(
                diagnostics=(
                    HookDiagnostic(
                        hazard,
                        kind="blocked",
                        rule_name=rule.name,
                        event=rule.event,
                        source_path=rule.source.path,
                        action_type=action.type,
                    ),
                )
            )
        if not action.trusted and not self._confirm(rule, action, context, summary="执行 shell 命令", detail=command):
            return HookActionResult(
                diagnostics=(
                    HookDiagnostic(
                        "用户拒绝 Hook shell 动作，命令未执行。",
                        kind="confirmation",
                        rule_name=rule.name,
                        event=rule.event,
                        source_path=rule.source.path,
                        action_type=action.type,
                    ),
                )
            )
        timeout = min(action.timeout_seconds or self.tool_context.command_timeout_seconds, self.tool_context.command_timeout_seconds)
        try:
            completed = subprocess.run(
                command,
                cwd=self.tool_context.workdir,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=_safe_env(),
            )
        except subprocess.TimeoutExpired as exc:
            stdout = sanitize_text(exc.stdout or "", self.tool_context)
            stderr = sanitize_text(exc.stderr or "", self.tool_context)
            return HookActionResult(
                diagnostics=(
                    HookDiagnostic(
                        f"Hook shell 动作超时（{timeout:g} 秒）。stdout={stdout} stderr={stderr}",
                        kind="action_error",
                        rule_name=rule.name,
                        event=rule.event,
                        source_path=rule.source.path,
                        action_type=action.type,
                    ),
                )
            )
        stdout, _ = clamp_text(sanitize_text(completed.stdout, self.tool_context), self.tool_context.max_output_chars)
        stderr, _ = clamp_text(sanitize_text(completed.stderr, self.tool_context), self.tool_context.max_output_chars)
        if completed.returncode != 0:
            return HookActionResult(
                diagnostics=(
                    HookDiagnostic(
                        f"Hook shell 动作退出码为 {completed.returncode}。stdout={stdout} stderr={stderr}",
                        kind="action_error",
                        rule_name=rule.name,
                        event=rule.event,
                        source_path=rule.source.path,
                        action_type=action.type,
                    ),
                )
            )
        return HookActionResult(
            diagnostics=(
                HookDiagnostic(
                    "Hook shell 动作执行完成。",
                    kind="action",
                    rule_name=rule.name,
                    event=rule.event,
                    source_path=rule.source.path,
                    action_type=action.type,
                ),
            )
        )

    def _execute_prompt_injection(
        self,
        rule: HookRule,
        action: HookAction,
        context: HookContext,
        action_index: int,
    ) -> HookActionResult:
        content = render_template(str(action.config["content"]), context)
        name = str(action.config.get("name") or f"hook_{rule.name}_{action_index}")
        priority = action.config.get("priority", 500)
        if not isinstance(priority, int):
            priority = 500
        return HookActionResult(
            dynamic_instructions=(
                DynamicInstruction(name=name, content=content, priority=priority, tag="hook"),
            )
        )

    def _execute_http(self, rule: HookRule, action: HookAction, context: HookContext) -> HookActionResult:
        method = str(action.config.get("method", "GET")).upper()
        url = render_template(str(action.config["url"]), context)
        headers = _render_headers(action.config.get("headers"), context)
        body_value = action.config.get("body")
        body = render_template(str(body_value), context) if body_value is not None else None
        timeout = action.timeout_seconds or self.tool_context.command_timeout_seconds
        if not url.startswith(("http://", "https://")):
            return HookActionResult(
                diagnostics=(
                    HookDiagnostic(
                        "Hook HTTP URL 必须以 http:// 或 https:// 开头。",
                        kind="blocked",
                        rule_name=rule.name,
                        event=rule.event,
                        source_path=rule.source.path,
                        action_type=action.type,
                    ),
                )
            )
        if not action.trusted and not self._confirm(rule, action, context, summary="发起 HTTP 请求", detail=f"{method} {url}"):
            return HookActionResult(
                diagnostics=(
                    HookDiagnostic(
                        "用户拒绝 Hook HTTP 动作，请求未发出。",
                        kind="confirmation",
                        rule_name=rule.name,
                        event=rule.event,
                        source_path=rule.source.path,
                        action_type=action.type,
                    ),
                )
            )
        try:
            status, response_text = self.http_client(method, url, headers, body, float(timeout))
        except Exception as exc:
            return HookActionResult(
                diagnostics=(
                    HookDiagnostic(
                        f"Hook HTTP 动作失败：{sanitize_text(str(exc), self.tool_context)}",
                        kind="action_error",
                        rule_name=rule.name,
                        event=rule.event,
                        source_path=rule.source.path,
                        action_type=action.type,
                    ),
                )
            )
        response_preview, _ = clamp_text(sanitize_text(response_text, self.tool_context), 500)
        kind = "action" if 200 <= status < 400 else "action_error"
        return HookActionResult(
            diagnostics=(
                HookDiagnostic(
                    f"Hook HTTP 动作完成：status={status} response={response_preview}",
                    kind=kind,
                    rule_name=rule.name,
                    event=rule.event,
                    source_path=rule.source.path,
                    action_type=action.type,
                ),
            )
        )

    def _confirm(self, rule: HookRule, action: HookAction, context: HookContext, *, summary: str, detail: str) -> bool:
        if self.confirm_action is None:
            return False
        safe_detail = sanitize_text(detail, self.tool_context)
        request = HookConfirmationRequest(
            rule_name=rule.name,
            event=context.event,
            action_type=action.type,
            summary=summary,
            detail=safe_detail,
        )
        try:
            return bool(self.confirm_action(request))
        except Exception:
            return False


def _render_headers(value: Any, context: HookContext) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    rendered: dict[str, str] = {}
    for key, item in value.items():
        if isinstance(key, str) and isinstance(item, str):
            rendered[key] = render_template(item, context)
    return rendered


def _default_http_client(method: str, url: str, headers: Mapping[str, str], body: str | None, timeout: float) -> tuple[int, str]:
    data = body.encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, headers=dict(headers), method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8", errors="replace")
            return int(response.status), text
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        return int(exc.code), text


def _safe_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if key in _ALLOWED_ENV_KEYS or not any(marker in upper for marker in _SECRET_ENV_MARKERS):
            env[key] = value
    return env
