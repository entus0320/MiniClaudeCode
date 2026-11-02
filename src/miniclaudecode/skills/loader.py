"""Skill discovery and Markdown frontmatter parsing."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from miniclaudecode.skills.core import (
    DirectoryToolSpec,
    SkillContextStrategy,
    SkillDefinition,
    SkillDiscoveryResult,
    SkillMode,
    SkillPackageKind,
    SkillSource,
    SkillSourceKind,
    SkillWarning,
)
from miniclaudecode.skills.validator import normalize_skill_metadata, validate_directory_tool_schema

ENTRY_FILENAMES = ("skill.md", "README.md")


class SkillLoader:
    """Load Skills from project, user, and builtin roots."""

    def __init__(
        self,
        *,
        project_root: str | Path | None = None,
        user_root: str | Path | None = None,
        builtin_root: str | Path | None = None,
    ) -> None:
        self.project_root = Path(project_root) if project_root is not None else None
        self.user_root = Path(user_root).expanduser() if user_root is not None else None
        self.builtin_root = Path(builtin_root) if builtin_root is not None else None

    def discover(self) -> SkillDiscoveryResult:
        """Discover Skills and apply source priority overrides."""

        candidates: list[SkillDefinition] = []
        warnings: list[SkillWarning] = []
        for kind, root in self._source_roots():
            loaded, source_warnings = self._scan_root(root, kind)
            candidates.extend(loaded)
            warnings.extend(source_warnings)

        by_name: dict[str, SkillDefinition] = {}
        overridden: list[str] = []
        for skill in sorted(candidates, key=lambda item: (item.name, item.source.priority, str(item.source.entry_path))):
            current = by_name.get(skill.name)
            if current is None or skill.source.priority > current.source.priority:
                if current is not None:
                    overridden.append(skill.name)
                by_name[skill.name] = skill
            elif current is not None and current.source.priority >= skill.source.priority:
                overridden.append(skill.name)

        skills = tuple(sorted(by_name.values(), key=lambda item: item.name))
        return SkillDiscoveryResult(
            skills=skills,
            warnings=tuple(warnings),
            overridden=tuple(sorted(set(overridden))),
        )

    def load_entry(self, entry_path: str | Path, kind: SkillSourceKind, root: str | Path | None = None) -> SkillDefinition:
        """Load one Skill from a known entry path."""

        path = Path(entry_path)
        root_path = Path(root) if root is not None else path.parent
        if path.is_dir():
            return self._load_directory_skill(path, kind, root_path)
        return self._load_file_skill(path, kind, root_path)

    def _source_roots(self) -> list[tuple[SkillSourceKind, Path]]:
        roots: list[tuple[SkillSourceKind, Path]] = []
        if self.builtin_root is not None:
            roots.append((SkillSourceKind.BUILTIN, self.builtin_root))
        if self.user_root is not None:
            roots.append((SkillSourceKind.USER, self.user_root))
        if self.project_root is not None:
            roots.append((SkillSourceKind.PROJECT, self.project_root))
        return roots

    def _scan_root(self, root: Path, kind: SkillSourceKind) -> tuple[list[SkillDefinition], list[SkillWarning]]:
        skills: list[SkillDefinition] = []
        warnings: list[SkillWarning] = []
        if not root.exists():
            return skills, warnings
        if not root.is_dir():
            return skills, [SkillWarning(f"Skill 根路径不是目录：{root}", path=root)]

        for child in sorted(root.iterdir(), key=lambda item: item.name.lower()):
            if child.name.startswith("."):
                continue
            try:
                if child.is_dir():
                    entry = _directory_entry(child)
                    if entry is None:
                        continue
                    skills.append(self._load_directory_skill(child, kind, root))
                elif child.suffix.lower() == ".md":
                    skills.append(self._load_file_skill(child, kind, root))
            except Exception as exc:
                warnings.append(SkillWarning(f"跳过无法解析的 Skill：{exc}", path=child))
        return skills, warnings

    def _load_file_skill(self, path: Path, kind: SkillSourceKind, root: Path) -> SkillDefinition:
        text = path.read_text(encoding="utf-8")
        metadata, body = parse_frontmatter(text, path)
        normalized = normalize_skill_metadata(metadata, path=path)
        source = SkillSource(
            kind=kind,
            root_path=root,
            package_path=path,
            entry_path=path,
            package_kind=SkillPackageKind.FILE,
        )
        return SkillDefinition(metadata=normalized, source=source, sop_markdown=body.strip())

    def _load_directory_skill(self, directory: Path, kind: SkillSourceKind, root: Path) -> SkillDefinition:
        entry = _directory_entry(directory)
        if entry is None:
            raise ValueError("目录型 Skill 缺少 skill.md 或 README.md")
        text = entry.read_text(encoding="utf-8")
        metadata, body = parse_frontmatter(text, entry)
        normalized = normalize_skill_metadata(metadata, path=entry)
        source = SkillSource(
            kind=kind,
            root_path=root,
            package_path=directory,
            entry_path=entry,
            package_kind=SkillPackageKind.DIRECTORY,
        )
        return SkillDefinition(
            metadata=normalized,
            source=source,
            sop_markdown=body.strip(),
            directory_tools=tuple(_load_directory_tools(directory, normalized.name)),
        )


def parse_frontmatter(text: str, path: Path) -> tuple[Mapping[str, Any], str]:
    """Parse YAML frontmatter and return raw metadata plus Markdown body."""

    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"Skill 文件缺少 YAML frontmatter：{path}")
    end_index = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end_index = index
            break
    if end_index is None:
        raise ValueError(f"Skill 文件 frontmatter 未闭合：{path}")
    raw_yaml = "\n".join(lines[1:end_index])
    try:
        data = yaml.safe_load(raw_yaml)
    except yaml.YAMLError as exc:
        raise ValueError(f"Skill frontmatter 不是合法 YAML：{path}") from exc
    if not isinstance(data, Mapping):
        raise ValueError(f"Skill frontmatter 必须是对象：{path}")
    body = "\n".join(lines[end_index + 1 :])
    return data, body


def _directory_entry(directory: Path) -> Path | None:
    for filename in ENTRY_FILENAMES:
        candidate = directory / filename
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _load_directory_tools(directory: Path, skill_name: str) -> list[DirectoryToolSpec]:
    tools_dir = directory / "tools"
    if not tools_dir.exists():
        return []
    if not tools_dir.is_dir():
        raise ValueError(f"目录型 Skill 的 tools 不是目录：{tools_dir}")
    specs: list[DirectoryToolSpec] = []
    for schema_path in sorted(tools_dir.glob("*.json"), key=lambda item: item.name.lower()):
        raw = json.loads(schema_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"工具 schema 必须是对象：{schema_path}")
        name = str(raw.get("name") or schema_path.stem)
        description = str(raw.get("description") or f"Directory Skill tool {skill_name}.{name}")
        input_schema = raw.get("input_schema", raw.get("schema"))
        if not isinstance(input_schema, dict):
            raise ValueError(f"工具 schema 缺少 input_schema：{schema_path}")
        script_value = raw.get("script", f"{schema_path.stem}.py")
        if not isinstance(script_value, str) or not script_value:
            raise ValueError(f"工具 script 必须是非空字符串：{schema_path}")
        script_path = tools_dir / script_value
        validate_directory_tool_schema(input_schema, path=schema_path)
        specs.append(
            DirectoryToolSpec(
                name=name,
                description=description,
                schema_path=schema_path,
                script_path=script_path,
                input_schema=input_schema,
            )
        )
    return specs
