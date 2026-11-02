"""AgentDefinition discovery and loading."""

from __future__ import annotations

from pathlib import Path

from miniclaudecode.subagents.core import (
    AgentDefinition,
    AgentDefinitionDiscoveryResult,
    AgentDefinitionPackageKind,
    AgentDefinitionSource,
    AgentDefinitionSourceKind,
    AgentDefinitionWarning,
)
from miniclaudecode.subagents.parser import parse_agent_frontmatter
from miniclaudecode.subagents.validator import normalize_agent_definition

ENTRY_FILENAMES = ("agent.md", "README.md")


class AgentDefinitionLoader:
    """Load AgentDefinitions from project, user, builtin, and plugin roots."""

    def __init__(
        self,
        *,
        project_root: str | Path | None = None,
        user_root: str | Path | None = None,
        builtin_root: str | Path | None = None,
        plugin_roots: tuple[str | Path, ...] = (),
        include_verify: bool = False,
    ) -> None:
        self.project_root = Path(project_root) if project_root is not None else None
        self.user_root = Path(user_root).expanduser() if user_root is not None else None
        self.builtin_root = Path(builtin_root) if builtin_root is not None else None
        self.plugin_roots = tuple(Path(root) for root in plugin_roots)
        self.include_verify = include_verify

    def discover(self) -> AgentDefinitionDiscoveryResult:
        """Discover AgentDefinitions and apply source priority overrides."""

        candidates: list[AgentDefinition] = []
        warnings: list[AgentDefinitionWarning] = []
        for kind, root in self._source_roots():
            loaded, source_warnings = self._scan_root(root, kind)
            candidates.extend(loaded)
            warnings.extend(source_warnings)

        by_name: dict[str, AgentDefinition] = {}
        overridden: list[str] = []
        for definition in sorted(candidates, key=lambda item: (item.name, item.source.priority, str(item.source.entry_path))):
            current = by_name.get(definition.name)
            if current is None or definition.source.priority > current.source.priority:
                if current is not None:
                    overridden.append(definition.name)
                by_name[definition.name] = definition
            elif current is not None and current.source.priority >= definition.source.priority:
                overridden.append(definition.name)

        definitions = tuple(sorted((definition for definition in by_name.values() if definition.enabled), key=lambda item: item.name))
        return AgentDefinitionDiscoveryResult(
            definitions=definitions,
            warnings=tuple(warnings),
            overridden=tuple(sorted(set(overridden))),
        )

    def load_entry(
        self,
        entry_path: str | Path,
        kind: AgentDefinitionSourceKind,
        root: str | Path | None = None,
    ) -> AgentDefinition:
        """Load one AgentDefinition from a known entry path."""

        path = Path(entry_path)
        root_path = Path(root) if root is not None else path.parent
        if path.is_dir():
            return self._load_directory_definition(path, kind, root_path)
        return self._load_file_definition(path, kind, root_path)

    def _source_roots(self) -> list[tuple[AgentDefinitionSourceKind, Path]]:
        roots: list[tuple[AgentDefinitionSourceKind, Path]] = []
        for root in self.plugin_roots:
            roots.append((AgentDefinitionSourceKind.PLUGIN, root))
        if self.builtin_root is not None:
            roots.append((AgentDefinitionSourceKind.BUILTIN, self.builtin_root))
        if self.user_root is not None:
            roots.append((AgentDefinitionSourceKind.USER, self.user_root))
        if self.project_root is not None:
            roots.append((AgentDefinitionSourceKind.PROJECT, self.project_root))
        return roots

    def _scan_root(self, root: Path, kind: AgentDefinitionSourceKind) -> tuple[list[AgentDefinition], list[AgentDefinitionWarning]]:
        definitions: list[AgentDefinition] = []
        warnings: list[AgentDefinitionWarning] = []
        if not root.exists():
            return definitions, warnings
        if not root.is_dir():
            return definitions, [AgentDefinitionWarning("AgentDefinition 根路径不是目录", path=root)]

        for child in sorted(root.iterdir(), key=lambda item: item.name.lower()):
            if child.name.startswith("."):
                continue
            try:
                if child.is_dir():
                    entry = _directory_entry(child)
                    if entry is None:
                        continue
                    definitions.append(self._load_directory_definition(child, kind, root))
                elif child.suffix.lower() == ".md":
                    definitions.append(self._load_file_definition(child, kind, root))
            except Exception as exc:
                warnings.append(AgentDefinitionWarning(f"跳过无法解析的 AgentDefinition：{exc}", path=child))
        return definitions, warnings

    def _load_file_definition(self, path: Path, kind: AgentDefinitionSourceKind, root: Path) -> AgentDefinition:
        text = path.read_text(encoding="utf-8")
        metadata, body = parse_agent_frontmatter(text, path)
        source = AgentDefinitionSource(
            kind=kind,
            root_path=root,
            package_path=path,
            entry_path=path,
            package_kind=AgentDefinitionPackageKind.FILE,
        )
        return normalize_agent_definition(metadata, body=body, source=source, path=path, include_verify=self.include_verify)

    def _load_directory_definition(self, directory: Path, kind: AgentDefinitionSourceKind, root: Path) -> AgentDefinition:
        entry = _directory_entry(directory)
        if entry is None:
            raise ValueError("目录型 AgentDefinition 缺少 agent.md 或 README.md")
        text = entry.read_text(encoding="utf-8")
        metadata, body = parse_agent_frontmatter(text, entry)
        source = AgentDefinitionSource(
            kind=kind,
            root_path=root,
            package_path=directory,
            entry_path=entry,
            package_kind=AgentDefinitionPackageKind.DIRECTORY,
        )
        return normalize_agent_definition(metadata, body=body, source=source, path=entry, include_verify=self.include_verify)


def _directory_entry(directory: Path) -> Path | None:
    for filename in ENTRY_FILENAMES:
        candidate = directory / filename
        if candidate.exists() and candidate.is_file():
            return candidate
    return None
