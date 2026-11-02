"""Path safety helpers for local MiniClaudeCode tools."""

from __future__ import annotations

from pathlib import Path

from miniclaudecode.tools.base import ToolContext, ToolResult

SENSITIVE_FILENAMES = frozenset({".env", "config.yaml", "id_rsa", "id_ed25519"})
SENSITIVE_SUFFIXES = (".env.", ".pem", ".key", ".crt")
EXPLICITLY_ALLOWED_FILENAMES = frozenset({".env.example", "config.example.yaml"})
POLICY_FILENAME = "miniclaudecode.policy.yaml"
IGNORED_DIRS = frozenset({".git", "__pycache__", ".venv", "venv", "minicode", "nimicode"})


def resolve_workspace_path(path_text: str, context: ToolContext, tool_name: str) -> Path | ToolResult:
    """Resolve a user path and ensure it stays within the workspace."""

    try:
        raw_path = Path(path_text)
        candidate = raw_path if raw_path.is_absolute() else context.workdir / raw_path
        resolved = candidate.resolve()
    except Exception as exc:
        return ToolResult.fail(tool_name=tool_name, error=f"路径解析失败：{exc}")

    if not _is_relative_to(resolved, context.workdir):
        return ToolResult.fail(tool_name=tool_name, error="路径超出项目工作目录，已拒绝访问。")
    return resolved


def relative_to_workdir(path: Path, context: ToolContext) -> str:
    """Return a stable POSIX-style path relative to the workspace."""

    return path.resolve().relative_to(context.workdir).as_posix()


def is_sensitive_path(path: Path, context: ToolContext) -> bool:
    """Return whether a path should be hidden from model-visible tool output."""

    try:
        relative = path.resolve().relative_to(context.workdir)
    except ValueError:
        return True

    parts = relative.parts
    if any(part in IGNORED_DIRS for part in parts):
        return True

    name = path.name
    if name in EXPLICITLY_ALLOWED_FILENAMES:
        return False
    if name in SENSITIVE_FILENAMES:
        return True
    if name.startswith(".env."):
        return True
    return any(name.endswith(suffix) for suffix in SENSITIVE_SUFFIXES[1:])


def is_ignored_path(path: Path, context: ToolContext) -> bool:
    """Return whether a path should be skipped by search/list tools."""

    try:
        relative = path.resolve().relative_to(context.workdir)
    except ValueError:
        return True
    return any(part in IGNORED_DIRS for part in relative.parts) or is_sensitive_path(path, context)


def sensitive_path_failure(tool_name: str) -> ToolResult:
    """Return the standard failure for sensitive paths."""

    return ToolResult.fail(tool_name=tool_name, error="目标路径可能包含密钥或本地配置，已拒绝访问。")


def is_policy_file_path(path: Path, context: ToolContext) -> bool:
    """Return whether a path points at the project policy file."""

    try:
        relative = path.resolve().relative_to(context.workdir)
    except ValueError:
        return False
    return relative.as_posix() == POLICY_FILENAME


def _is_relative_to(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents
