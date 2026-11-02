"""Transcript helpers for AgentTeam members."""

from __future__ import annotations

from pathlib import Path

from miniclaudecode.session_store import SessionStore


class TeamTranscriptStore:
    """Create SessionStore instances rooted at one member transcript directory."""

    def __init__(self, root: str | Path, *, provider_name: str = "team", model: str = "team") -> None:
        self.root = Path(root)
        self.provider_name = provider_name
        self.model = model

    def for_agent(self, agent_id: str) -> SessionStore:
        return SessionStore(self.root / agent_id, provider_name=self.provider_name, model=self.model, session_id=agent_id)
