"""Per-agentID mailbox storage for AgentTeam messages."""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from miniclaudecode.teams.core import TeamMessage, TeamMessageKind


class MailboxStore:
    """Append/read mailbox messages stored as one JSONL file per agentID."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def mailbox_path(self, agent_id: str) -> Path:
        return self.root / f"{agent_id}.jsonl"

    def append(self, message: TeamMessage) -> Path:
        path = self.mailbox_path(message.to_agent_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(_message_to_dict(message), ensure_ascii=False, separators=(",", ":")) + "\n")
        return path

    def read_all(self, agent_id: str) -> tuple[TeamMessage, ...]:
        path = self.mailbox_path(agent_id)
        if not path.exists():
            return ()
        messages: list[TeamMessage] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            raw = json.loads(line)
            if isinstance(raw, dict):
                messages.append(_message_from_dict(raw))
        return tuple(messages)

    def read_unprocessed(self, agent_id: str) -> tuple[TeamMessage, ...]:
        return tuple(message for message in self.read_all(agent_id) if message.processed_at is None)

    def mark_processed(self, agent_id: str, message_ids: tuple[str, ...]) -> None:
        ids = set(message_ids)
        messages = [replace(message, processed_at=message.processed_at or 0.0) if message.message_id in ids else message for message in self.read_all(agent_id)]
        path = self.mailbox_path(agent_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text("".join(json.dumps(_message_to_dict(message), ensure_ascii=False, separators=(",", ":")) + "\n" for message in messages), encoding="utf-8")
        tmp.replace(path)


def _message_to_dict(message: TeamMessage) -> dict[str, Any]:
    payload = asdict(message)
    payload["kind"] = message.kind.value
    return payload


def _message_from_dict(raw: dict[str, Any]) -> TeamMessage:
    return TeamMessage(
        message_id=str(raw.get("message_id", "")),
        team_id=str(raw.get("team_id", "")),
        from_agent_id=str(raw["from_agent_id"]) if raw.get("from_agent_id") is not None else None,
        from_member=str(raw["from_member"]) if raw.get("from_member") is not None else None,
        to_agent_id=str(raw.get("to_agent_id", "")),
        to_member=str(raw["to_member"]) if raw.get("to_member") is not None else None,
        kind=TeamMessageKind(str(raw.get("kind", TeamMessageKind.TEXT.value))),
        summary=str(raw.get("summary", "")),
        body=str(raw.get("body", "")),
        payload=dict(raw.get("payload", {})) if isinstance(raw.get("payload", {}), dict) else {},
        created_at=float(raw.get("created_at", 0.0)),
        processed_at=float(raw["processed_at"]) if raw.get("processed_at") is not None else None,
    )
