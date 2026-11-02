"""Run command tool."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from typing import Any

from miniclaudecode.policy import detect_dangerous_command
from miniclaudecode.tools.base import Tool, ToolContext, ToolResult, clamp_text, require_string, sanitize_text

_ALLOWED_ENV_KEYS = {"PATH", "SystemRoot", "WINDIR", "ComSpec", "TEMP", "TMP", "HOME", "USERPROFILE"}
_SECRET_ENV_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")


class RunCommandTool(Tool):
    name = "run_command"
    description = "Run a shell command in the project workspace only when no dedicated tool fits. Prefer read_file, glob_files, and grep_content for file work; commands require user confirmation and have a timeout."
    input_schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Command to run from the project root."},
            "timeout_seconds": {"type": "number", "description": "Optional timeout in seconds."},
        },
        "required": ["command"],
        "additionalProperties": False,
    }
    requires_confirmation = True

    def execute(self, arguments: Mapping[str, Any], context: ToolContext) -> ToolResult:
        command = require_string(arguments, "command", self.name)
        if isinstance(command, ToolResult):
            return command
        timeout = arguments.get("timeout_seconds", context.command_timeout_seconds)
        if not isinstance(timeout, int | float) or timeout <= 0:
            return ToolResult.fail(tool_name=self.name, error="参数 `timeout_seconds` 必须是正数。")
        timeout = min(float(timeout), context.command_timeout_seconds)
        hazard = detect_dangerous_command(command)
        if hazard is not None:
            return ToolResult.fail(
                tool_name=self.name,
                error=hazard,
                metadata={"blocked_by": "policy", "policy_action": "deny", "policy_source": "builtin"},
            )

        try:
            completed = subprocess.run(
                command,
                cwd=context.workdir,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=_safe_env(),
            )
        except subprocess.TimeoutExpired as exc:
            stdout = sanitize_text(exc.stdout or "", context)
            stderr = sanitize_text(exc.stderr or "", context)
            return ToolResult.fail(
                tool_name=self.name,
                error=f"命令执行超时（{timeout:g} 秒）。",
                metadata={"stdout": stdout, "stderr": stderr, "timeout_seconds": timeout},
            )
        except Exception as exc:
            return ToolResult.fail(tool_name=self.name, error=f"命令执行失败：{exc}")

        stdout, stdout_truncated = clamp_text(sanitize_text(completed.stdout, context), context.max_output_chars)
        stderr, stderr_truncated = clamp_text(sanitize_text(completed.stderr, context), context.max_output_chars)
        success = completed.returncode == 0
        metadata = {
            "return_code": completed.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
        }
        if success:
            return ToolResult.ok(tool_name=self.name, content="命令执行完成。", metadata=metadata)
        return ToolResult.fail(tool_name=self.name, error=f"命令退出码为 {completed.returncode}。", metadata=metadata)


def _safe_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if key in _ALLOWED_ENV_KEYS or not any(marker in upper for marker in _SECRET_ENV_MARKERS):
            env[key] = value
    return env
