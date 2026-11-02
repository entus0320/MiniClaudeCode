"""Name validation and branch mapping for managed worktrees."""

from __future__ import annotations

import re

from miniclaudecode.worktrees.core import WorktreeConfig, WorktreeError

_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._/-]+$")
_BRANCH_SEGMENT_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")


def validate_worktree_slug(slug: str, config: WorktreeConfig | None = None) -> str:
    """Validate and normalize an untrusted worktree slug."""

    return validate_worktree_name(slug, config)


def validate_worktree_name(name: str, config: WorktreeConfig | None = None) -> str:
    """Validate and normalize a user-provided worktree name."""

    cfg = config or WorktreeConfig()
    value = name.strip()
    if not value:
        raise WorktreeError("Worktree 名称不能为空。")
    if len(value) > cfg.max_name_length:
        raise WorktreeError(f"Worktree 名称长度不能超过 {cfg.max_name_length}。")
    if "\\" in value:
        raise WorktreeError("Worktree 名称不能包含反斜杠。")
    if value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        raise WorktreeError("Worktree 名称不能是绝对路径。")
    if _NAME_PATTERN.fullmatch(value) is None:
        raise WorktreeError("Worktree 名称只能包含字母、数字、点、下划线、短横线和 /。")
    parts = value.split("/")
    for part in parts:
        if part == "":
            raise WorktreeError("Worktree 名称不能包含空路径段。")
        if part in {".", ".."}:
            raise WorktreeError("Worktree 名称不能包含 . 或 .. 路径段。")
        if len(part) > cfg.max_segment_length:
            raise WorktreeError(f"Worktree 名称单段长度不能超过 {cfg.max_segment_length}。")
    return value


def branch_name_for_worktree(name: str, config: WorktreeConfig | None = None) -> str:
    """Convert a validated logical worktree name to a safe branch name."""

    cfg = config or WorktreeConfig()
    value = validate_worktree_name(name, cfg)
    flattened_parts = []
    for part in value.split("/"):
        cleaned = _BRANCH_SEGMENT_PATTERN.sub("-", part).strip(".-")
        flattened_parts.append(cleaned or "x")
    flattened = "-".join(flattened_parts)
    prefix = cfg.branch_prefix.strip()
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    return f"{prefix}{flattened}"
