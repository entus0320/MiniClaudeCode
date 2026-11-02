"""JSON-RPC 2.0 helpers for MCP tool servers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from itertools import count
from typing import Any

JsonValue = dict[str, Any] | list[Any] | str | int | float | bool | None
RequestId = str | int


class JsonRpcProtocolError(Exception):
    """Raised when a JSON-RPC message is malformed."""


class JsonRpcRemoteError(Exception):
    """Raised when a JSON-RPC response contains an error object."""

    def __init__(self, code: int | None, message: str, data: Any = None) -> None:
        self.code = code
        self.data = data
        prefix = f"JSON-RPC error {code}: " if code is not None else "JSON-RPC error: "
        super().__init__(prefix + message)


@dataclass(frozen=True)
class JsonRpcResponse:
    """A parsed JSON-RPC response."""

    id: RequestId
    result: Any = None
    error: JsonRpcRemoteError | None = None

    def raise_for_error(self) -> None:
        if self.error is not None:
            raise self.error


@dataclass(frozen=True)
class JsonRpcNotification:
    """A parsed JSON-RPC notification."""

    method: str
    params: Any = None


class RequestIdGenerator:
    """Generate monotonically increasing JSON-RPC request ids."""

    def __init__(self) -> None:
        self._counter = count(1)

    def next(self) -> int:
        """Return the next request id."""

        return next(self._counter)


def make_request(method: str, params: Any | None = None, request_id: RequestId | None = None) -> dict[str, Any]:
    """Build a JSON-RPC request object."""

    if not isinstance(method, str) or method.strip() == "":
        raise JsonRpcProtocolError("JSON-RPC method must be a non-empty string.")
    if request_id is None:
        raise JsonRpcProtocolError("JSON-RPC request id is required.")
    request: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        request["params"] = params
    return request


def serialize_message(message: dict[str, Any]) -> str:
    """Serialize one JSON-RPC message as compact JSON."""

    try:
        return json.dumps(message, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise JsonRpcProtocolError("JSON-RPC message is not JSON serializable.") from exc


def parse_message(raw: str | bytes) -> JsonRpcResponse | JsonRpcNotification:
    """Parse one JSON-RPC response or notification."""

    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise JsonRpcProtocolError("JSON-RPC message is not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise JsonRpcProtocolError("JSON-RPC message must be an object.")
    if payload.get("jsonrpc") != "2.0":
        raise JsonRpcProtocolError("JSON-RPC message must contain jsonrpc='2.0'.")

    if "id" in payload:
        return _parse_response(payload)
    return _parse_notification(payload)


def _parse_response(payload: dict[str, Any]) -> JsonRpcResponse:
    response_id = payload["id"]
    if not isinstance(response_id, (str, int)) or isinstance(response_id, bool):
        raise JsonRpcProtocolError("JSON-RPC response id must be a string or integer.")
    has_result = "result" in payload
    has_error = "error" in payload
    if has_result == has_error:
        raise JsonRpcProtocolError("JSON-RPC response must contain exactly one of result or error.")
    if has_error:
        error_payload = payload["error"]
        if not isinstance(error_payload, dict):
            raise JsonRpcProtocolError("JSON-RPC error must be an object.")
        code = error_payload.get("code")
        if code is not None and (not isinstance(code, int) or isinstance(code, bool)):
            raise JsonRpcProtocolError("JSON-RPC error code must be an integer.")
        message = error_payload.get("message")
        if not isinstance(message, str) or message == "":
            raise JsonRpcProtocolError("JSON-RPC error message must be a non-empty string.")
        return JsonRpcResponse(
            id=response_id,
            error=JsonRpcRemoteError(code, message, error_payload.get("data")),
        )
    return JsonRpcResponse(id=response_id, result=payload.get("result"))


def _parse_notification(payload: dict[str, Any]) -> JsonRpcNotification:
    method = payload.get("method")
    if not isinstance(method, str) or method == "":
        raise JsonRpcProtocolError("JSON-RPC notification method must be a non-empty string.")
    return JsonRpcNotification(method=method, params=payload.get("params"))
