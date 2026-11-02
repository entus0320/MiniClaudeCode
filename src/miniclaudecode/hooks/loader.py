"""Hook YAML discovery and loading."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from miniclaudecode.hooks.core import HookDiagnostic, HookDiscoveryResult, HookRule, HookSource, HookSourceKind
from miniclaudecode.hooks.validator import normalize_hook_rule


class HookLoader:
    """Load Hook rules from project and user roots."""

    def __init__(self, *, project_root: str | Path | None = None, user_root: str | Path | None = None) -> None:
        self.project_root = Path(project_root) if project_root is not None else None
        self.user_root = Path(user_root).expanduser() if user_root is not None else None

    def discover(self) -> HookDiscoveryResult:
        """Discover Hook rules and apply source priority overrides."""

        candidates: list[HookRule] = []
        diagnostics: list[HookDiagnostic] = []
        for kind, root in self._source_roots():
            loaded, source_diagnostics = self._scan_root(root, kind)
            candidates.extend(loaded)
            diagnostics.extend(source_diagnostics)

        by_name: dict[str, HookRule] = {}
        overridden: list[str] = []
        for rule in sorted(candidates, key=lambda item: (item.name, item.source.priority, str(item.source.path), item.declaration_index)):
            current = by_name.get(rule.name)
            if current is None or rule.source.priority > current.source.priority:
                if current is not None:
                    overridden.append(rule.name)
                    diagnostics.append(
                        HookDiagnostic(
                            f"规则被覆盖：{current.source.kind.value} -> {rule.source.kind.value}",
                            kind="override",
                            rule_name=rule.name,
                            event=rule.event,
                            source_path=rule.source.path,
                        )
                    )
                by_name[rule.name] = rule
            elif current is not None:
                overridden.append(rule.name)
                diagnostics.append(
                    HookDiagnostic(
                        f"规则被覆盖或重复，已跳过：{rule.source.kind.value}",
                        kind="override",
                        rule_name=rule.name,
                        event=rule.event,
                        source_path=rule.source.path,
                    )
                )
        rules = tuple(sorted(by_name.values(), key=lambda item: (-item.source.priority, str(item.source.path).lower(), item.declaration_index)))
        return HookDiscoveryResult(rules=rules, diagnostics=tuple(diagnostics), overridden=tuple(sorted(set(overridden))))

    def _source_roots(self) -> list[tuple[HookSourceKind, Path]]:
        roots: list[tuple[HookSourceKind, Path]] = []
        if self.user_root is not None:
            roots.append((HookSourceKind.USER, self.user_root))
        if self.project_root is not None:
            roots.append((HookSourceKind.PROJECT, self.project_root))
        return roots

    def _scan_root(self, root: Path, kind: HookSourceKind) -> tuple[list[HookRule], list[HookDiagnostic]]:
        if not root.exists():
            return [], []
        if not root.is_dir():
            return [], [HookDiagnostic(f"Hook 根路径不是目录：{root}", kind="load_error", source_path=root)]
        rules: list[HookRule] = []
        diagnostics: list[HookDiagnostic] = []
        paths = [*root.glob("*.yaml"), *root.glob("*.yml")]
        for path in sorted(paths, key=lambda item: item.name.lower()):
            loaded, file_diagnostics = self._load_file(path, kind, root)
            rules.extend(loaded)
            diagnostics.extend(file_diagnostics)
        return rules, diagnostics

    def _load_file(self, path: Path, kind: HookSourceKind, root: Path) -> tuple[list[HookRule], list[HookDiagnostic]]:
        diagnostics: list[HookDiagnostic] = []
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception as exc:
            return [], [HookDiagnostic(f"Hook YAML 解析失败：{exc}", kind="load_error", source_path=path)]
        if raw is None:
            return [], []
        if not isinstance(raw, Mapping):
            return [], [HookDiagnostic("Hook YAML 根节点必须是对象。", kind="load_error", source_path=path)]
        raw_rules = raw.get("rules")
        if not isinstance(raw_rules, list):
            return [], [HookDiagnostic("Hook YAML 缺少 rules 数组。", kind="load_error", source_path=path)]
        source = HookSource(kind=kind, root_path=root, path=path)
        rules: list[HookRule] = []
        for index, raw_rule in enumerate(raw_rules, start=1):
            if not isinstance(raw_rule, Mapping):
                diagnostics.append(HookDiagnostic(f"Hook rule #{index} 必须是对象。", kind="load_error", source_path=path))
                continue
            try:
                rules.append(normalize_hook_rule(raw_rule, source=source, declaration_index=index))
            except Exception as exc:
                rule_name = raw_rule.get("name") if isinstance(raw_rule.get("name"), str) else None
                diagnostics.append(HookDiagnostic(str(exc), kind="load_error", rule_name=rule_name, source_path=path))
        return rules, diagnostics
