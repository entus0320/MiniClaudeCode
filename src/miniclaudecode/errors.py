"""Safe error helpers for MiniClaudeCode."""

from __future__ import annotations


def redact_secret(text: str, secret: str | None) -> str:
    """Replace a known secret value in text with a safe placeholder."""

    if secret is None or secret == "":
        return text
    return text.replace(secret, "[REDACTED]")


def safe_message(message: str, *, secret: str | None = None) -> str:
    """Return a user-facing error message with known secrets removed."""

    return redact_secret(message, secret)
