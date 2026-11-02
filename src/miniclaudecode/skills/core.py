"""Core Skill models for MiniClaudeCode."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class SkillSourceKind(str, Enum):
    """Where a Skill was loaded from."""

    PROJECT = "project"
    USER = "user"
    BUILTIN = "builtin"


class SkillPackageKind(str, Enum):
    """How a Skill is packaged on disk."""

    FILE = "file"
    DIRECTORY = "directory"


class SkillMode(str, Enum):
    """How a Skill should execute."""

    SHARED = "shared"
    ISOLATED = "isolated"


class SkillContextStrategy(str, Enum):
    """How an isolated Skill would receive history in a later stage."""

    FULL_SUMMARY = "full-summary"
    RECENT = "recent"
    EMPTY = "empty"


class SkillError(Exception):
    """Base class for Skill errors."""


class SkillDiscoveryError(SkillError):
    """Raised when Skill discovery cannot safely continue."""


class SkillValidationError(SkillError):
    """Raised when a Skill is invalid."""


@dataclass(frozen=True)
class SkillSource:
    """Source location and priority for one Skill."""

    kind: SkillSourceKind
    root_path: Path
    package_path: Path
    entry_path: Path
    package_kind: SkillPackageKind

    @property
    def priority(self) -> int:
        if self.kind is SkillSourceKind.PROJECT:
            return 30
        if self.kind is SkillSourceKind.USER:
            return 20
        return 10


@dataclass(frozen=True)
class DirectoryToolSpec:
    """One tool bundled inside a directory Skill."""

    name: str
    description: str
    schema_path: Path
    script_path: Path
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class SkillMetadata:
    """Validated Skill metadata from frontmatter."""

    name: str
    description: str
    tools: tuple[str, ...] = ()
    mode: SkillMode = SkillMode.SHARED
    model: str | None = None
    context_strategy: SkillContextStrategy = SkillContextStrategy.FULL_SUMMARY
    recent_messages: int | None = None


@dataclass(frozen=True)
class SkillWarning:
    """Non-fatal Skill loading issue."""

    message: str
    path: Path | None = None
    skill_name: str | None = None

    def render(self) -> str:
        parts = [self.message]
        if self.skill_name:
            parts.append(f"Skill: {self.skill_name}")
        if self.path is not None:
            parts.append(f"Path: {self.path}")
        return " | ".join(parts)


@dataclass(frozen=True)
class SkillDefinition:
    """A complete loaded Skill definition."""

    metadata: SkillMetadata
    source: SkillSource
    sop_markdown: str
    directory_tools: tuple[DirectoryToolSpec, ...] = ()
    warnings: tuple[SkillWarning, ...] = ()

    @property
    def name(self) -> str:
        return self.metadata.name

    @property
    def description(self) -> str:
        return self.metadata.description


@dataclass(frozen=True)
class SkillReloadResult:
    """Observable result from reloading Skills."""

    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    overridden: tuple[str, ...] = ()
    warnings: tuple[SkillWarning, ...] = ()
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.error is None


@dataclass(frozen=True)
class SkillDiscoveryResult:
    """Raw discovery result before registry replacement."""

    skills: tuple[SkillDefinition, ...] = ()
    warnings: tuple[SkillWarning, ...] = ()
    overridden: tuple[str, ...] = field(default_factory=tuple)
