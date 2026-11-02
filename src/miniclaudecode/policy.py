"""Tool execution policy for MiniClaudeCode."""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

from miniclaudecode.tools.base import Tool, ToolCall, ToolContext, ToolResult, require_string
from miniclaudecode.tools.paths import (
    is_policy_file_path,
    is_sensitive_path,
    relative_to_workdir,
    resolve_workspace_path,
)

POLICY_FILENAME = "miniclaudecode.policy.yaml"
READ_TOOLS = frozenset({"read_file", "glob_files", "grep_content"})
WRITE_PATH_TOOLS = frozenset({"write_file", "edit_file"})
PATH_ARGUMENT_TOOLS = frozenset({"read_file", "write_file", "edit_file", "grep_content"})


class PolicyConfigError(Exception):
    """Raised when a project policy file cannot be loaded safely."""


class PolicyAction(str, Enum):
    """Execution action decided by the policy engine."""

    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class PermissionMode(str, Enum):
    """Global policy mode for default decisions."""

    STRICT = "strict"
    DEFAULT = "default"
    PERMISSIVE = "permissive"


@dataclass(frozen=True)
class PolicyDecision:
    """Decision returned before a tool is executed."""

    action: PolicyAction
    reason: str
    source: str = "builtin"
    matched_rule: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def allow(cls, reason: str, *, source: str = "builtin", matched_rule: str | None = None) -> "PolicyDecision":
        return cls(PolicyAction.ALLOW, reason, source=source, matched_rule=matched_rule)

    @classmethod
    def deny(cls, reason: str, *, source: str = "builtin", matched_rule: str | None = None) -> "PolicyDecision":
        return cls(PolicyAction.DENY, reason, source=source, matched_rule=matched_rule)

    @classmethod
    def ask(cls, reason: str, *, source: str = "builtin", matched_rule: str | None = None) -> "PolicyDecision":
        return cls(PolicyAction.ASK, reason, source=source, matched_rule=matched_rule)


@dataclass(frozen=True)
class PolicyRule:
    """A simple tool/path/command rule."""

    action: PolicyAction
    tool: str | None = None
    path: str | None = None
    command: str | None = None
    command_contains: str | None = None
    command_regex: str | None = None
    reason: str = "Policy rule matched."
    name: str | None = None
    source: str = "project"

    def matches(self, call: ToolCall, details: "PolicyCallDetails") -> bool:
        if self.tool is not None and self.tool not in {"*", call.name}:
            return False
        if self.path is not None:
            if details.relative_path is None or not fnmatch.fnmatch(details.relative_path, self.path):
                return False
        if self.command is not None:
            if details.command != self.command:
                return False
        if self.command_contains is not None:
            if details.command is None or self.command_contains not in details.command:
                return False
        if self.command_regex is not None:
            if details.command is None or re.search(self.command_regex, details.command) is None:
                return False
        return True

    @property
    def label(self) -> str:
        return self.name or self.reason


@dataclass
class SessionPolicy:
    """In-memory policy rules that live only for the current process."""

    rules: list[PolicyRule] = field(default_factory=list)

    def add_allow_for_call(self, call: ToolCall, tool: Tool, context: ToolContext) -> None:
        details = build_call_details(call, tool, context)
        rule = PolicyRule(
            action=PolicyAction.ALLOW,
            tool=tool.name,
            path=details.relative_path,
            command=details.command,
            reason="本会话已允许同类工具调用。",
            name=f"session:{tool.name}",
            source="session",
        )
        self.rules.insert(0, rule)


@dataclass(frozen=True)
class PolicyContext:
    """Context used by PolicyEngine when evaluating one tool call."""

    workdir: Path
    permission_mode: PermissionMode = PermissionMode.DEFAULT
    project_rules: tuple[PolicyRule, ...] = ()
    session_policy: SessionPolicy = field(default_factory=SessionPolicy)
    plan_only: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "workdir", Path(self.workdir).resolve())
        if not isinstance(self.permission_mode, PermissionMode):
            object.__setattr__(self, "permission_mode", PermissionMode(str(self.permission_mode)))


@dataclass(frozen=True)
class ProjectPolicy:
    """Policy loaded from a project-level policy file."""

    mode: PermissionMode = PermissionMode.DEFAULT
    rules: tuple[PolicyRule, ...] = ()


@dataclass(frozen=True)
class PolicyCallDetails:
    """Normalized facts extracted from a tool call for policy matching."""

    relative_path: str | None = None
    command: str | None = None


class PolicyEngine:
    """Evaluate local tool calls before execution."""

    def evaluate(self, call: ToolCall, tool: Tool, tool_context: ToolContext, policy_context: PolicyContext) -> PolicyDecision:
        hard_decision = self._hard_boundary_decision(call, tool, tool_context, policy_context)
        if hard_decision is not None:
            return hard_decision

        details = build_call_details(call, tool, tool_context)
        for rule in policy_context.session_policy.rules:
            if rule.matches(call, details):
                return PolicyDecision(rule.action, rule.reason, source="session", matched_rule=rule.label)
        for rule in policy_context.project_rules:
            if rule.matches(call, details):
                return PolicyDecision(rule.action, rule.reason, source="project", matched_rule=rule.label)

        return self._default_decision(tool, policy_context.permission_mode)

    def _hard_boundary_decision(
        self,
        call: ToolCall,
        tool: Tool,
        tool_context: ToolContext,
        policy_context: PolicyContext,
    ) -> PolicyDecision | None:
        if policy_context.plan_only and tool.name not in READ_TOOLS:
            return PolicyDecision.deny(f"plan-only 模式下禁止执行 {tool.name}，真实操作未执行。")

        path_decision = _path_hard_boundary(call, tool, tool_context)
        if path_decision is not None:
            return path_decision

        if tool.name == "run_command":
            command = call.arguments.get("command")
            if isinstance(command, str):
                hazard = detect_dangerous_command(command)
                if hazard is not None:
                    return PolicyDecision.deny(hazard)
        return None

    def _default_decision(self, tool: Tool, mode: PermissionMode) -> PolicyDecision:
        if tool.name in READ_TOOLS:
            return PolicyDecision.allow("安全读取类工具默认允许。", source="mode")
        if mode is PermissionMode.PERMISSIVE and not tool.requires_confirmation:
            return PolicyDecision.allow("permissive 模式默认允许该工具。", source="mode")
        if mode is PermissionMode.PERMISSIVE and tool.name in WRITE_PATH_TOOLS | {"run_command"}:
            return PolicyDecision.allow("permissive 模式默认允许普通写入、编辑或命令。", source="mode")
        if tool.requires_confirmation:
            return PolicyDecision.ask("该工具需要用户确认。", source="mode")
        if mode is PermissionMode.STRICT:
            return PolicyDecision.ask("strict 模式下未明确允许的工具调用需要确认。", source="mode")
        return PolicyDecision.allow("默认策略允许该工具。", source="mode")


_DANGEROUS_COMMAND_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\brm\s+-[A-Za-z]*r[A-Za-z]*f[A-Za-z]*\s+(?:/|\.|\*)\b", re.IGNORECASE), "破坏性删除命令被策略拒绝。"),
    (re.compile(r"\bdel\s+/s\b", re.IGNORECASE), "破坏性 Windows 删除命令被策略拒绝。"),
    (re.compile(r"\brmdir\s+/s\b", re.IGNORECASE), "破坏性 Windows 删除命令被策略拒绝。"),
    (re.compile(r"\bRemove-Item\b.*\b-Recurse\b", re.IGNORECASE), "破坏性 PowerShell 删除命令被策略拒绝。"),
    (re.compile(r"\b(format|mkfs|diskpart)\b", re.IGNORECASE), "磁盘破坏类命令被策略拒绝。"),
    (re.compile(r"\bdd\b.*\bif=", re.IGNORECASE), "高风险 dd 命令被策略拒绝。"),
    (re.compile(r"\bcurl\b.*\|\s*(?:sh|bash)\b", re.IGNORECASE), "远程脚本下载即执行被策略拒绝。"),
    (re.compile(r"\bwget\b.*\|\s*(?:sh|bash)\b", re.IGNORECASE), "远程脚本下载即执行被策略拒绝。"),
    (re.compile(r"\bInvoke-WebRequest\b.*\|\s*iex\b", re.IGNORECASE), "PowerShell 远程脚本执行被策略拒绝。"),
    (re.compile(r"\bpowershell\b.*\biex\b", re.IGNORECASE), "PowerShell 远程脚本执行被策略拒绝。"),
    (re.compile(r"\bgit\s+reset\s+--hard\b", re.IGNORECASE), "git reset --hard 被策略拒绝。"),
    (re.compile(r"\bgit\s+clean\s+-[A-Za-z]*f[A-Za-z]*d[A-Za-z]*\b", re.IGNORECASE), "git clean -fd 被策略拒绝。"),
    (re.compile(r"\bgit\s+push\b.*(?:--force\b|-f\b)", re.IGNORECASE), "git force push 被策略拒绝。"),
    (re.compile(r"\bnpm\s+publish\b", re.IGNORECASE), "公开发布命令被策略拒绝。"),
    (re.compile(r">>?") , ""),
)

_SENSITIVE_REDIRECT_PATTERN = re.compile(
    r">>?\s*(?:\.env(?:\.[\w.-]+)?|config\.yaml|miniclaudecode\.policy\.yaml)\b",
    re.IGNORECASE,
)


def detect_dangerous_command(command: str) -> str | None:
    """Return a denial reason if a shell command is clearly dangerous."""

    if _SENSITIVE_REDIRECT_PATTERN.search(command):
        return "命令尝试写入敏感配置文件，已被策略拒绝。"
    for pattern, reason in _DANGEROUS_COMMAND_PATTERNS:
        if reason and pattern.search(command):
            return reason
    return None


def build_call_details(call: ToolCall, tool: Tool, context: ToolContext) -> PolicyCallDetails:
    command = call.arguments.get("command") if tool.name == "run_command" else None
    command_text = command if isinstance(command, str) else None
    relative_path = _relative_path_for_call(call, tool, context)
    return PolicyCallDetails(relative_path=relative_path, command=command_text)


def _relative_path_for_call(call: ToolCall, tool: Tool, context: ToolContext) -> str | None:
    if tool.name == "glob_files":
        pattern = call.arguments.get("pattern")
        return pattern if isinstance(pattern, str) else None
    if tool.name == "grep_content":
        path_text = call.arguments.get("path", ".")
    elif tool.name in PATH_ARGUMENT_TOOLS:
        path_text = call.arguments.get("path")
    else:
        return None
    if not isinstance(path_text, str) or path_text == "":
        return None
    resolved = resolve_workspace_path(path_text, context, tool.name)
    if isinstance(resolved, ToolResult):
        return None
    return relative_to_workdir(resolved, context)


def _path_hard_boundary(call: ToolCall, tool: Tool, context: ToolContext) -> PolicyDecision | None:
    if tool.name == "glob_files":
        pattern = require_string(call.arguments, "pattern", tool.name)
        if isinstance(pattern, ToolResult):
            return None
        raw_path = Path(pattern)
        if raw_path.is_absolute() or ".." in raw_path.parts:
            return PolicyDecision.deny("glob pattern 超出项目工作目录，已拒绝访问。")
        return None

    if tool.name == "grep_content":
        glob = call.arguments.get("glob", "**/*")
        if isinstance(glob, str):
            raw_glob = Path(glob)
            if raw_glob.is_absolute() or ".." in raw_glob.parts:
                return PolicyDecision.deny("grep glob 超出项目工作目录，已拒绝访问。")

    if tool.name not in PATH_ARGUMENT_TOOLS:
        return None

    path_text = call.arguments.get("path", ".") if tool.name == "grep_content" else call.arguments.get("path")
    if not isinstance(path_text, str) or path_text == "":
        return None
    resolved = resolve_workspace_path(path_text, context, tool.name)
    if isinstance(resolved, ToolResult):
        return PolicyDecision.deny(resolved.error or "路径不安全，已拒绝访问。")
    if tool.name in WRITE_PATH_TOOLS and is_policy_file_path(resolved, context):
        return PolicyDecision.deny("项目权限策略文件不允许通过工具写入或编辑。")
    if is_sensitive_path(resolved, context):
        return PolicyDecision.deny("目标路径可能包含密钥或本地配置，已拒绝访问。")
    return None


def load_project_policy(path: str | Path) -> ProjectPolicy:
    """Load a project policy file. Missing files return the default policy."""

    policy_path = Path(path)
    if not policy_path.exists():
        return ProjectPolicy()
    try:
        with policy_path.open("r", encoding="utf-8") as file:
            raw = yaml.safe_load(file)
    except yaml.YAMLError as exc:
        raise PolicyConfigError(f"Policy file '{policy_path}' is not valid YAML.") from exc
    except OSError as exc:
        raise PolicyConfigError(f"Policy file '{policy_path}' could not be read.") from exc

    if raw is None:
        return ProjectPolicy()
    if not isinstance(raw, Mapping):
        raise PolicyConfigError(f"Policy file '{policy_path}' must contain a YAML object.")

    mode = _parse_mode(raw.get("mode", PermissionMode.DEFAULT.value), f"{policy_path}.mode")
    raw_rules = raw.get("rules", [])
    if not isinstance(raw_rules, Sequence) or isinstance(raw_rules, (str, bytes)):
        raise PolicyConfigError(f"{policy_path}.rules must be a YAML list.")

    rules: list[PolicyRule] = []
    for index, raw_rule in enumerate(raw_rules, start=1):
        if not isinstance(raw_rule, Mapping):
            raise PolicyConfigError(f"{policy_path}.rules[{index}] must be a YAML object.")
        rules.append(_parse_rule(raw_rule, f"{policy_path}.rules[{index}]"))
    return ProjectPolicy(mode=mode, rules=tuple(rules))


def _parse_rule(raw: Mapping[str, Any], field_name: str) -> PolicyRule:
    action = _parse_action(raw.get("action"), f"{field_name}.action")
    tool = _optional_str(raw.get("tool"), f"{field_name}.tool")
    path = _optional_str(raw.get("path"), f"{field_name}.path")
    command = _optional_str(raw.get("command"), f"{field_name}.command")
    command_contains = _optional_str(raw.get("command_contains"), f"{field_name}.command_contains")
    command_regex = _optional_str(raw.get("command_regex"), f"{field_name}.command_regex")
    if command_regex is not None:
        try:
            re.compile(command_regex)
        except re.error as exc:
            raise PolicyConfigError(f"{field_name}.command_regex is not a valid regular expression.") from exc
    reason = _optional_str(raw.get("reason"), f"{field_name}.reason") or "Project policy rule matched."
    name = _optional_str(raw.get("name"), f"{field_name}.name")
    return PolicyRule(
        action=action,
        tool=tool,
        path=path,
        command=command,
        command_contains=command_contains,
        command_regex=command_regex,
        reason=reason,
        name=name,
        source="project",
    )


def _parse_action(value: Any, field_name: str) -> PolicyAction:
    if not isinstance(value, str):
        raise PolicyConfigError(f"{field_name} must be one of: allow, ask, deny.")
    try:
        return PolicyAction(value.strip())
    except ValueError as exc:
        raise PolicyConfigError(f"{field_name} must be one of: allow, ask, deny.") from exc


def _parse_mode(value: Any, field_name: str) -> PermissionMode:
    if not isinstance(value, str):
        raise PolicyConfigError(f"{field_name} must be one of: strict, default, permissive.")
    try:
        return PermissionMode(value.strip())
    except ValueError as exc:
        raise PolicyConfigError(f"{field_name} must be one of: strict, default, permissive.") from exc


def _optional_str(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or value.strip() == "":
        raise PolicyConfigError(f"{field_name} must be a non-empty string when provided.")
    return value.strip()
