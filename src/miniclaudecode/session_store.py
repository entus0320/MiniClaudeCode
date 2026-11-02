"""Append-only session transcript storage and recovery."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from miniclaudecode.session import Message, ToolResultOffloadInfo
from miniclaudecode.tools.base import ToolCall

SCHEMA_VERSION = 1
TRANSCRIPT_FILENAME = "transcript.jsonl"
META_FILENAME = "meta.json"
INDEX_FILENAME = "index.json"


@dataclass(frozen=True)
class SessionMeta:
    """Small session summary used by session lists."""

    schema_version: int
    session_id: str
    title: str
    summary: str
    created_at: str
    updated_at: str
    last_active_at: str
    message_count: int
    event_count: int
    provider_name: str
    model: str
    last_resume_notice_at: str | None = None


@dataclass(frozen=True)
class LoadedSession:
    """Recovered messages plus non-fatal recovery warnings."""

    meta: SessionMeta
    messages: tuple[Message, ...]
    warnings: tuple[str, ...] = ()


class SessionStore:
    """Persist one session as JSONL transcript plus a small meta file."""

    def __init__(
        self,
        root: str | Path,
        *,
        provider_name: str,
        model: str,
        session_id: str | None = None,
        enabled: bool = True,
    ) -> None:
        self.root = Path(root)
        self.provider_name = provider_name
        self.model = model
        self.enabled = enabled
        self.session_id = session_id or new_session_id()
        self.session_dir = self.root / self.session_id
        self.transcript_path = self.session_dir / TRANSCRIPT_FILENAME
        self.meta_path = self.session_dir / META_FILENAME
        self.index_path = self.root / INDEX_FILENAME
        self._seq = 0
        self._message_count = 0
        self._event_count = 0
        now = utc_now_iso()
        self._meta = SessionMeta(
            schema_version=SCHEMA_VERSION,
            session_id=self.session_id,
            title="新会话",
            summary="",
            created_at=now,
            updated_at=now,
            last_active_at=now,
            message_count=0,
            event_count=0,
            provider_name=provider_name,
            model=model,
        )
        if self.enabled:
            self.session_dir.mkdir(parents=True, exist_ok=True)
            if self.meta_path.exists():
                existing = _read_meta(self.meta_path)
                if existing is not None:
                    self._meta = existing
                    self._message_count = existing.message_count
                    self._event_count = existing.event_count
                    self._seq = existing.event_count
            self._write_meta()

    def record_message(self, message: Message) -> None:
        """Append one committed message event."""

        if not self.enabled:
            return
        self._append_event({"type": "message", "message": message_to_dict(message)})
        self._message_count += 1
        if self._message_count == 1:
            self._meta = _replace_meta(self._meta, title=_title_from_message(message))
        self._touch_meta()

    def record_history_replaced(self, messages: list[Message] | tuple[Message, ...]) -> None:
        """Append a full-history replacement event for compaction/resume notices."""

        if not self.enabled:
            return
        payload = {"type": "history_replaced", "messages": [message_to_dict(message) for message in messages]}
        self._append_event(payload)
        self._message_count = len(messages)
        self._touch_meta()

    def list_sessions(self, limit: int = 20) -> list[SessionMeta]:
        """Return recent sessions from meta files without reading transcripts."""

        if not self.root.exists():
            return []
        metas: list[SessionMeta] = []
        for path in self.root.glob(f"*/{META_FILENAME}"):
            meta = _read_meta(path)
            if meta is not None:
                metas.append(meta)
        metas.sort(key=lambda item: item.updated_at, reverse=True)
        return metas[:limit]

    def load_session(self, session_id: str) -> LoadedSession:
        """Load and recover one session transcript."""

        session_dir = self.root / session_id
        meta = _read_meta(session_dir / META_FILENAME)
        if meta is None:
            now = utc_now_iso()
            meta = SessionMeta(
                schema_version=SCHEMA_VERSION,
                session_id=session_id,
                title=session_id,
                summary="",
                created_at=now,
                updated_at=now,
                last_active_at=now,
                message_count=0,
                event_count=0,
                provider_name=self.provider_name,
                model=self.model,
            )
        messages, warnings = load_messages_from_jsonl(session_dir / TRANSCRIPT_FILENAME)
        recovered, truncate_warning = truncate_incomplete_tool_groups(messages)
        if truncate_warning is not None:
            warnings.append(truncate_warning)
        return LoadedSession(meta=meta, messages=tuple(recovered), warnings=tuple(warnings))

    def _append_event(self, event: dict[str, Any]) -> None:
        self._seq += 1
        self._event_count += 1
        payload = {
            "schema_version": SCHEMA_VERSION,
            "seq": self._seq,
            "timestamp": utc_now_iso(),
            **event,
        }
        with self.transcript_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")

    def _touch_meta(self) -> None:
        now = utc_now_iso()
        self._meta = _replace_meta(
            self._meta,
            updated_at=now,
            last_active_at=now,
            message_count=self._message_count,
            event_count=self._event_count,
        )
        self._write_meta()

    def _write_meta(self) -> None:
        self.session_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.meta_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(self._meta), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.meta_path)
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(json.dumps({"schema_version": SCHEMA_VERSION}, ensure_ascii=False), encoding="utf-8")


def new_session_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{uuid.uuid4().hex[:8]}"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def message_to_dict(message: Message) -> dict[str, Any]:
    return {
        "role": message.role,
        "content": message.content,
        "tool_calls": [tool_call_to_dict(call) for call in message.tool_calls],
        "tool_call_id": message.tool_call_id,
        "tool_name": message.tool_name,
        "synthetic_kind": message.synthetic_kind,
        "tool_result_offload": offload_to_dict(message.tool_result_offload),
    }


def message_from_dict(raw: dict[str, Any]) -> Message:
    offload_raw = raw.get("tool_result_offload")
    return Message(
        role=str(raw.get("role", "")),
        content=str(raw.get("content", "")),
        tool_calls=[tool_call_from_dict(item) for item in raw.get("tool_calls", []) if isinstance(item, dict)],
        tool_call_id=_optional_str(raw.get("tool_call_id")),
        tool_name=_optional_str(raw.get("tool_name")),
        synthetic_kind=_optional_str(raw.get("synthetic_kind")),
        tool_result_offload=offload_from_dict(offload_raw) if isinstance(offload_raw, dict) else None,
    )


def tool_call_to_dict(call: ToolCall) -> dict[str, Any]:
    return {
        "id": call.id,
        "name": call.name,
        "arguments": call.arguments,
        "raw_arguments": call.raw_arguments,
        "provider": call.provider,
        "parse_error": call.parse_error,
    }


def tool_call_from_dict(raw: dict[str, Any]) -> ToolCall:
    arguments = raw.get("arguments")
    return ToolCall(
        id=str(raw.get("id", "")),
        name=str(raw.get("name", "")),
        arguments=arguments if isinstance(arguments, dict) else {},
        raw_arguments=_optional_str(raw.get("raw_arguments")),
        provider=_optional_str(raw.get("provider")),
        parse_error=_optional_str(raw.get("parse_error")),
    )


def offload_to_dict(info: ToolResultOffloadInfo | None) -> dict[str, Any] | None:
    if info is None:
        return None
    return asdict(info)


def offload_from_dict(raw: dict[str, Any]) -> ToolResultOffloadInfo:
    return ToolResultOffloadInfo(
        tool_name=str(raw.get("tool_name", "")),
        tool_call_id=_optional_str(raw.get("tool_call_id")),
        path=str(raw.get("path", "")),
        bytes=int(raw.get("bytes", 0)),
        sha256=str(raw.get("sha256", "")),
        reason=str(raw.get("reason", "")),
        original_chars=int(raw.get("original_chars", 0)),
        slimmed_chars=int(raw.get("slimmed_chars", 0)),
        released_chars=int(raw.get("released_chars", 0)),
        original_estimated_tokens=int(raw.get("original_estimated_tokens", 0)),
        slimmed_estimated_tokens=int(raw.get("slimmed_estimated_tokens", 0)),
        released_estimated_tokens=int(raw.get("released_estimated_tokens", 0)),
        content_preview=str(raw.get("content_preview", "")),
        error_preview=_optional_str(raw.get("error_preview")),
    )


def load_messages_from_jsonl(path: str | Path) -> tuple[list[Message], list[str]]:
    transcript = Path(path)
    warnings: list[str] = []
    messages: list[Message] = []
    if not transcript.exists():
        return messages, warnings
    for line_number, line in enumerate(transcript.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            warnings.append(f"跳过无法解析的 JSONL 行 {line_number}。")
            continue
        try:
            event_type = event.get("type")
            if event_type == "message":
                raw_message = event.get("message")
                if isinstance(raw_message, dict):
                    messages.append(message_from_dict(raw_message))
            elif event_type == "history_replaced":
                raw_messages = event.get("messages")
                if isinstance(raw_messages, list):
                    messages = [message_from_dict(item) for item in raw_messages if isinstance(item, dict)]
        except (TypeError, ValueError) as exc:
            warnings.append(f"跳过无效会话事件 {line_number}: {exc}")
    return messages, warnings


def truncate_incomplete_tool_groups(messages: list[Message]) -> tuple[list[Message], str | None]:
    index = 0
    while index < len(messages):
        message = messages[index]
        if message.role == "assistant" and message.tool_calls:
            expected = {call.id for call in message.tool_calls}
            cursor = index + 1
            while cursor < len(messages) and messages[cursor].role == "tool" and messages[cursor].tool_call_id in expected:
                expected.discard(messages[cursor].tool_call_id or "")
                cursor += 1
            if expected:
                return messages[:index], "恢复时发现未配对的 assistant tool_use，已截断到最后完整位置。"
            index = cursor
            continue
        if message.role == "tool":
            return messages[:index], "恢复时发现孤立 tool_result，已截断到最后完整位置。"
        index += 1
    return messages, None


def _read_meta(path: Path) -> SessionMeta | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    try:
        return SessionMeta(
            schema_version=int(raw.get("schema_version", SCHEMA_VERSION)),
            session_id=str(raw.get("session_id", path.parent.name)),
            title=str(raw.get("title", "")),
            summary=str(raw.get("summary", "")),
            created_at=str(raw.get("created_at", "")),
            updated_at=str(raw.get("updated_at", "")),
            last_active_at=str(raw.get("last_active_at", "")),
            message_count=int(raw.get("message_count", 0)),
            event_count=int(raw.get("event_count", 0)),
            provider_name=str(raw.get("provider_name", "")),
            model=str(raw.get("model", "")),
            last_resume_notice_at=_optional_str(raw.get("last_resume_notice_at")),
        )
    except (TypeError, ValueError):
        return None


def _replace_meta(meta: SessionMeta, **changes: Any) -> SessionMeta:
    data = asdict(meta)
    data.update(changes)
    return SessionMeta(**data)


def _title_from_message(message: Message) -> str:
    text = " ".join(message.content.split())
    if not text:
        return "新会话"
    return text[:40]


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None
