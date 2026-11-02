"""Project and user instruction file loading."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from miniclaudecode.prompting import PromptModule, PromptStability

_INCLUDE_PATTERN = re.compile(r"^\s*@include\s+(.+?)\s*$")


@dataclass(frozen=True)
class InstructionConfig:
    """Configuration for loading user and project instruction files."""

    enabled: bool = True
    project_file: Path = Path("MiniClaudeCode.md")
    user_file: Path = Path("~/.miniclaudecode/CLAUDE.md")
    max_include_depth: int = 3


@dataclass(frozen=True)
class InstructionDocument:
    """One loaded instruction document after safe include expansion."""

    level: str
    path: Path
    content: str
    included_paths: tuple[Path, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class InstructionBundle:
    """Loaded instructions in model-facing priority order."""

    documents: tuple[InstructionDocument, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def text(self) -> str:
        """Render all instruction documents as one prompt supplement."""

        return render_instruction_bundle(self)

    def to_prompt_module(self) -> PromptModule | None:
        """Convert instructions to a dynamic prompt module if any content exists."""

        rendered = self.text
        if not rendered.strip():
            return None
        return PromptModule(
            name="long_term_instructions",
            priority=80,
            stability=PromptStability.DYNAMIC,
            content=rendered,
        )


def load_instruction_bundle(config: InstructionConfig, *, workdir: str | Path) -> InstructionBundle:
    """Load project and user instructions with project instructions first."""

    if not config.enabled:
        return InstructionBundle()

    root = Path(workdir)
    project_path = _resolve_root_file(config.project_file, root=root)
    user_path = Path(config.user_file).expanduser()
    documents: list[InstructionDocument] = []
    warnings: list[str] = []

    project = _load_root_document(
        level="project",
        path=project_path,
        max_include_depth=config.max_include_depth,
    )
    if project is not None:
        documents.append(project)
        warnings.extend(project.warnings)

    user = _load_root_document(
        level="user",
        path=user_path,
        max_include_depth=config.max_include_depth,
    )
    if user is not None:
        documents.append(user)
        warnings.extend(user.warnings)

    return InstructionBundle(documents=tuple(documents), warnings=tuple(warnings))


def render_instruction_bundle(bundle: InstructionBundle) -> str:
    """Render instructions with explicit priority and source labels."""

    if not bundle.documents:
        return ""
    parts = [
        "<long-term-instructions>",
        "以下是启动时加载的长期指令。优先级：项目级 > 用户级；冲突时必须遵循项目级指令。",
    ]
    for document in bundle.documents:
        label = "项目级指令" if document.level == "project" else "用户级指令"
        parts.append(f"\n## {label}: {document.path}")
        parts.append(document.content.strip())
    parts.append("</long-term-instructions>")
    return "\n".join(part for part in parts if part != "")


def _resolve_root_file(path: Path, *, root: Path) -> Path:
    if path.is_absolute():
        return path
    return root / path


def _load_root_document(*, level: str, path: Path, max_include_depth: int) -> InstructionDocument | None:
    if not path.exists():
        return None
    warnings: list[str] = []
    included: list[Path] = []
    try:
        content = _load_file_with_includes(
            path=path,
            depth=0,
            max_depth=max_include_depth,
            stack=(),
            included=included,
            warnings=warnings,
        )
    except OSError as exc:
        return InstructionDocument(
            level=level,
            path=path,
            content="",
            warnings=(f"无法读取指令文件 {path}: {exc}",),
        )
    return InstructionDocument(
        level=level,
        path=path,
        content=content,
        included_paths=tuple(included),
        warnings=tuple(warnings),
    )


def _load_file_with_includes(
    *,
    path: Path,
    depth: int,
    max_depth: int,
    stack: tuple[Path, ...],
    included: list[Path],
    warnings: list[str],
) -> str:
    resolved = path.resolve()
    if resolved in stack:
        warnings.append(f"跳过循环 include: {path}")
        return ""
    text = path.read_text(encoding="utf-8")
    lines: list[str] = []
    for line in text.splitlines():
        match = _INCLUDE_PATTERN.match(line)
        if match is None:
            lines.append(line)
            continue
        include_text = _load_include(
            raw=match.group(1),
            base_dir=path.parent,
            depth=depth,
            max_depth=max_depth,
            stack=(*stack, resolved),
            included=included,
            warnings=warnings,
        )
        if include_text:
            lines.append(include_text)
    return "\n".join(lines)


def _load_include(
    *,
    raw: str,
    base_dir: Path,
    depth: int,
    max_depth: int,
    stack: tuple[Path, ...],
    included: list[Path],
    warnings: list[str],
) -> str:
    value = raw.strip().strip('"').strip("'")
    if not value:
        warnings.append(f"跳过空 include: {base_dir}")
        return ""
    include_path = Path(value)
    if include_path.is_absolute():
        warnings.append(f"跳过绝对路径 include: {value}")
        return ""
    if ".." in include_path.parts:
        warnings.append(f"跳过包含 .. 的 include: {value}")
        return ""
    if depth >= max_depth:
        warnings.append(f"跳过超过最大深度的 include: {value}")
        return ""

    candidate = base_dir / include_path
    try:
        base_resolved = base_dir.resolve()
        candidate_resolved = candidate.resolve()
        candidate_resolved.relative_to(base_resolved)
    except (OSError, ValueError):
        warnings.append(f"跳过跳出目录的 include: {value}")
        return ""
    if not candidate.exists():
        warnings.append(f"include 文件不存在: {candidate}")
        return ""

    included.append(candidate_resolved)
    try:
        return _load_file_with_includes(
            path=candidate,
            depth=depth + 1,
            max_depth=max_depth,
            stack=stack,
            included=included,
            warnings=warnings,
        )
    except OSError as exc:
        warnings.append(f"无法读取 include 文件 {candidate}: {exc}")
        return ""
