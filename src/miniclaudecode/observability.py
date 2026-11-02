"""Generation observability helpers for MiniClaudeCode."""

from __future__ import annotations

import time
from dataclasses import dataclass
from threading import Lock

DEFAULT_GENERATION_TIMEOUT_SECONDS = 120.0


@dataclass(frozen=True)
class GenerationWatchConfig:
    """Configuration for non-interrupting generation observation."""

    enabled: bool = True
    idle_timeout_seconds: float = DEFAULT_GENERATION_TIMEOUT_SECONDS
    total_timeout_seconds: float = DEFAULT_GENERATION_TIMEOUT_SECONDS


@dataclass(frozen=True)
class GenerationWatchEvent:
    """A user-visible observation about a running model generation."""

    kind: str
    label: str
    phase: str
    elapsed_seconds: float
    idle_for_seconds: float
    round_index: int | None = None


class GenerationWatchdog:
    """Track model-generation elapsed and idle time without changing behavior."""

    def __init__(
        self,
        config: GenerationWatchConfig,
        *,
        phase: str,
        label: str,
        round_index: int | None = None,
        now: float | None = None,
    ) -> None:
        self.config = config
        self.phase = phase
        self.label = label
        self.round_index = round_index
        started_at = _now() if now is None else now
        self._started_at = started_at
        self._last_activity_at = started_at
        self._finished = False
        self._idle_alerted = False
        self._total_alerted = False
        self._recovered_alerted = False
        self._had_timeout = False
        self._has_activity_after_timeout = False
        self._lock = Lock()

    def mark_activity(self, *, now: float | None = None) -> None:
        """Record that the provider produced a visible stream event."""

        current = _now() if now is None else now
        with self._lock:
            if self._finished or not self.config.enabled:
                return
            self._last_activity_at = current
            if self._had_timeout:
                self._has_activity_after_timeout = True

    def poll(self, *, now: float | None = None) -> list[GenerationWatchEvent]:
        """Return newly observed timeout or recovery events."""

        current = _now() if now is None else now
        events: list[GenerationWatchEvent] = []
        with self._lock:
            if self._finished or not self.config.enabled:
                return []
            elapsed = max(0.0, current - self._started_at)
            idle_for = max(0.0, current - self._last_activity_at)
            if not self._idle_alerted and idle_for >= self.config.idle_timeout_seconds:
                self._idle_alerted = True
                self._had_timeout = True
                events.append(self._event("idle_timeout", elapsed, idle_for))
            if not self._total_alerted and elapsed >= self.config.total_timeout_seconds:
                self._total_alerted = True
                self._had_timeout = True
                events.append(self._event("total_timeout", elapsed, idle_for))
            if self._has_activity_after_timeout and not self._recovered_alerted:
                self._recovered_alerted = True
                events.append(self._event("recovered", elapsed, idle_for))
        return events

    def finish(self) -> None:
        """Stop observing this generation."""

        with self._lock:
            self._finished = True

    def _event(self, kind: str, elapsed: float, idle_for: float) -> GenerationWatchEvent:
        return GenerationWatchEvent(
            kind=kind,
            label=self.label,
            phase=self.phase,
            elapsed_seconds=elapsed,
            idle_for_seconds=idle_for,
            round_index=self.round_index,
        )


def _now() -> float:
    return time.monotonic()
