"""Persistent store for AgentTeam state."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from miniclaudecode.teams.core import AgentTeam, AgentTeamConfig, TeamBackendKind, TeamDiagnostic, TeamError, TeamMember, TeamMemberStatus
from miniclaudecode.teams.ids import validate_team_name

SCHEMA_VERSION = 1
STATE_FILENAME = "state.json"


class AgentTeamStore:
    """Read and write AgentTeam state under one root directory."""

    def __init__(self, root: str | Path, config: AgentTeamConfig | None = None) -> None:
        self.root = Path(root)
        self.config = config or AgentTeamConfig(root_dir=Path(root))

    def team_root(self, name: str) -> Path:
        return self.root / validate_team_name(name)

    def create(self, name: str, *, lead_id: str | None = None) -> AgentTeam:
        team_name = validate_team_name(name)
        now = time.time()
        team = AgentTeam(
            team_id=team_name,
            name=team_name,
            lead_id=lead_id,
            root=self.team_root(team_name),
            config=self.config,
            created_at=now,
            updated_at=now,
        )
        self.save(team)
        return team

    def load(self, name: str) -> AgentTeam:
        path = self.team_root(name) / STATE_FILENAME
        if not path.exists():
            raise TeamError(f"AgentTeam 不存在：{name}")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise TeamError(f"AgentTeam 状态文件无法读取：{exc}") from exc
        if not isinstance(raw, dict):
            raise TeamError("AgentTeam 状态文件格式错误：根节点不是对象。")
        return _team_from_dict(raw, self.config)

    def save(self, team: AgentTeam) -> None:
        payload = _team_to_dict(replace(team, updated_at=time.time()))
        team.root.mkdir(parents=True, exist_ok=True)
        tmp = (team.root / STATE_FILENAME).with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(team.root / STATE_FILENAME)

    def list_teams(self) -> tuple[AgentTeam, ...]:
        if not self.root.exists():
            return ()
        teams: list[AgentTeam] = []
        for state_path in self.root.glob(f"*/{STATE_FILENAME}"):
            try:
                raw = json.loads(state_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    teams.append(_team_from_dict(raw, self.config))
            except Exception:
                teams.append(
                    AgentTeam(
                        team_id=state_path.parent.name,
                        name=state_path.parent.name,
                        lead_id=None,
                        root=state_path.parent,
                        config=self.config,
                        archived=True,
                        diagnostics=(TeamDiagnostic("AgentTeam 状态文件无法读取。", state_path),),
                    )
                )
        return tuple(sorted(teams, key=lambda team: team.name))

    def archive(self, name: str) -> AgentTeam:
        team = self.load(name)
        archived = replace(team, archived=True, updated_at=time.time())
        self.save(archived)
        return archived


def _team_to_dict(team: AgentTeam) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "team_id": team.team_id,
        "name": team.name,
        "lead_id": team.lead_id,
        "root": str(team.root),
        "config": _config_to_dict(team.config),
        "members": [_member_to_dict(member) for member in team.members],
        "created_at": team.created_at,
        "updated_at": team.updated_at,
        "archived": team.archived,
        "diagnostics": [_diagnostic_to_dict(diagnostic) for diagnostic in team.diagnostics],
    }


def _team_from_dict(raw: dict[str, Any], default_config: AgentTeamConfig) -> AgentTeam:
    config = _config_from_dict(raw.get("config"), default_config)
    diagnostics = tuple(_diagnostic_from_dict(item) for item in raw.get("diagnostics", []) if isinstance(item, dict))
    return AgentTeam(
        team_id=str(raw.get("team_id", raw.get("name", ""))),
        name=str(raw.get("name", raw.get("team_id", ""))),
        lead_id=str(raw["lead_id"]) if raw.get("lead_id") is not None else None,
        root=Path(str(raw.get("root", ""))),
        config=config,
        members=tuple(_member_from_dict(item) for item in raw.get("members", []) if isinstance(item, dict)),
        created_at=float(raw.get("created_at", 0.0)),
        updated_at=float(raw.get("updated_at", 0.0)),
        archived=bool(raw.get("archived", False)),
        diagnostics=diagnostics,
    )


def _config_to_dict(config: AgentTeamConfig) -> dict[str, Any]:
    payload = asdict(config)
    for key in ("root_dir", "mailbox_dir", "transcript_dir", "tasks_file", "registry_file"):
        payload[key] = str(payload[key])
    return payload


def _config_from_dict(value: Any, default: AgentTeamConfig) -> AgentTeamConfig:
    if not isinstance(value, dict):
        return default
    return AgentTeamConfig(
        enabled=bool(value.get("enabled", default.enabled)),
        root_dir=Path(str(value.get("root_dir", default.root_dir))),
        backend_priority=tuple(str(item) for item in value.get("backend_priority", default.backend_priority)),
        allow_backend_fallback=bool(value.get("allow_backend_fallback", default.allow_backend_fallback)),
        mailbox_dir=Path(str(value.get("mailbox_dir", default.mailbox_dir))),
        transcript_dir=Path(str(value.get("transcript_dir", default.transcript_dir))),
        tasks_file=Path(str(value.get("tasks_file", default.tasks_file))),
        registry_file=Path(str(value.get("registry_file", default.registry_file))),
        default_permission_mode=str(value.get("default_permission_mode", default.default_permission_mode)),
        coordinator_enabled=bool(value.get("coordinator_enabled", default.coordinator_enabled)),
    )


def _member_to_dict(member: TeamMember) -> dict[str, Any]:
    payload = asdict(member)
    payload["backend"] = member.backend.value
    payload["status"] = member.status.value
    payload["workdir"] = str(member.workdir)
    payload["mailbox_path"] = str(member.mailbox_path)
    payload["transcript_path"] = str(member.transcript_path)
    return payload


def _member_from_dict(raw: dict[str, Any]) -> TeamMember:
    return TeamMember(
        name=str(raw.get("name", "")),
        role=str(raw.get("role", "")),
        agent_id=str(raw.get("agent_id", "")),
        backend=TeamBackendKind(str(raw.get("backend", TeamBackendKind.IN_PROCESS.value))),
        workdir=Path(str(raw.get("workdir", "."))),
        mailbox_path=Path(str(raw.get("mailbox_path", ""))),
        transcript_path=Path(str(raw.get("transcript_path", ""))),
        permission_mode=str(raw.get("permission_mode", "default")),
        status=TeamMemberStatus(str(raw.get("status", TeamMemberStatus.PENDING.value))),
        backend_ref=dict(raw.get("backend_ref", {})) if isinstance(raw.get("backend_ref", {}), dict) else {},
        created_at=float(raw.get("created_at", 0.0)),
        updated_at=float(raw.get("updated_at", 0.0)),
        last_active_at=float(raw.get("last_active_at", 0.0)),
        metadata=dict(raw.get("metadata", {})) if isinstance(raw.get("metadata", {}), dict) else {},
    )


def _diagnostic_to_dict(diagnostic: TeamDiagnostic) -> dict[str, str | None]:
    return {"message": diagnostic.message, "path": str(diagnostic.path) if diagnostic.path is not None else None}


def _diagnostic_from_dict(raw: dict[str, Any]) -> TeamDiagnostic:
    return TeamDiagnostic(str(raw.get("message", "")), Path(str(raw["path"])) if raw.get("path") else None)
