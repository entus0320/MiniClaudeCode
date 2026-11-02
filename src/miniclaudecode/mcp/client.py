"""MCP server connection lifecycle."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from miniclaudecode import __version__
from miniclaudecode.config import McpServerConfig
from miniclaudecode.mcp.jsonrpc import JsonRpcRemoteError
from miniclaudecode.mcp.transport import Transport, TransportClosed, TransportError, TransportTimeout, create_transport


class McpServerError(Exception):
    """Raised when an MCP server cannot complete an operation."""


class ConnectionState(str, Enum):
    """Observable connection state for an MCP server."""

    NEW = "new"
    READY = "ready"
    BROKEN = "broken"
    CLOSED = "closed"


@dataclass(frozen=True)
class McpToolDescription:
    """A tool description returned by an MCP server."""

    server_name: str
    name: str
    description: str
    input_schema: dict[str, Any]
    parallel_safe: bool = False


@dataclass(frozen=True)
class McpCallResult:
    """A normalized result from an MCP tools/call response."""

    success: bool
    content: str = ""
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class McpServerConnection:
    """Manage one MCP server connection and its tools."""

    def __init__(
        self,
        config: McpServerConfig,
        *,
        transport_factory=create_transport,  # type: ignore[no-untyped-def]
    ) -> None:
        self.config = config
        self._transport_factory = transport_factory
        self._transport: Transport | None = None
        self.state = ConnectionState.NEW
        self.tools: list[McpToolDescription] = []

    def connect(self) -> None:
        """Open the server, initialize it, and refresh its tool list."""

        self._connect_with_retry()

    def refresh_tools(self) -> list[McpToolDescription]:
        """Refresh and return the server's MCP tool descriptions."""

        result = self._request_with_recovery("tools/list", {}, retry_kind="list")
        tools = _parse_tools_list(self.config.name, result, self.config.safe_tools)
        self.tools = tools
        return tools

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> McpCallResult:
        """Call one MCP tool by its original server-side name."""

        params = {"name": tool_name, "arguments": arguments}
        safe = tool_name in self.config.safe_tools
        try:
            result = self._request_with_recovery(
                "tools/call",
                params,
                retry_kind="safe_call" if safe else "unsafe_call",
            )
        except McpServerError as exc:
            return McpCallResult(success=False, error=str(exc), metadata={"server": self.config.name})
        return _parse_call_result(result, server_name=self.config.name)

    def close(self) -> None:
        """Close the underlying transport."""

        if self._transport is not None:
            self._transport.close()
        self.state = ConnectionState.CLOSED

    def _connect_with_retry(self) -> None:
        last_error: Exception | None = None
        for _attempt in range(self.config.max_reconnect_attempts + 1):
            self._close_transport()
            try:
                self._transport = self._transport_factory(self.config)
                self._transport.open()
                self._initialize_once()
                result = self._request_once("tools/list", {})
                self.tools = _parse_tools_list(self.config.name, result, self.config.safe_tools)
                self.state = ConnectionState.READY
                return
            except JsonRpcRemoteError as exc:
                self.state = ConnectionState.BROKEN
                raise McpServerError(str(exc)) from exc
            except (TransportError, McpServerError) as exc:
                self.state = ConnectionState.BROKEN
                last_error = exc
                continue
        raise McpServerError(str(last_error) if last_error is not None else "MCP server connection failed.")

    def _initialize_once(self) -> None:
        params = {
            "clientInfo": {"name": "MiniClaudeCode", "version": __version__},
            "protocolVersion": "2024-11-05",
            "capabilities": {},
        }
        self._request_once("initialize", params)

    def _request_with_recovery(self, method: str, params: Any, *, retry_kind: str) -> Any:
        if self.state is not ConnectionState.READY or self._transport is None or self._transport.broken:
            self._connect_with_retry()
        try:
            return self._request_once(method, params)
        except JsonRpcRemoteError as exc:
            raise McpServerError(str(exc)) from exc
        except (TransportClosed, TransportTimeout, TransportError) as exc:
            self.state = ConnectionState.BROKEN
            if retry_kind == "unsafe_call":
                raise McpServerError(
                    "MCP 工具调用连接中断。为避免重复副作用，该工具未标记为 safe/idempotent，MiniClaudeCode 未自动重发请求。"
                ) from exc
            self._connect_with_retry()
            try:
                return self._request_once(method, params)
            except JsonRpcRemoteError as retry_exc:
                raise McpServerError(str(retry_exc)) from retry_exc
            except (TransportClosed, TransportTimeout, TransportError) as retry_exc:
                self.state = ConnectionState.BROKEN
                raise McpServerError(str(retry_exc)) from retry_exc

    def _request_once(self, method: str, params: Any) -> Any:
        if self._transport is None:
            raise TransportClosed("MCP transport is not open.")
        return self._transport.request(method, params)

    def _close_transport(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None


def _parse_tools_list(server_name: str, value: Any, safe_tools: frozenset[str]) -> list[McpToolDescription]:
    raw_tools = value.get("tools") if isinstance(value, dict) else value
    if raw_tools is None:
        return []
    if not isinstance(raw_tools, list):
        raise McpServerError("tools/list result must contain a tools list.")
    tools: list[McpToolDescription] = []
    for item in raw_tools:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or name == "":
            continue
        description = item.get("description") if isinstance(item.get("description"), str) else "MCP tool."
        schema = item.get("inputSchema", item.get("input_schema"))
        if not isinstance(schema, dict):
            continue
        tools.append(
            McpToolDescription(
                server_name=server_name,
                name=name,
                description=description,
                input_schema=dict(schema),
                parallel_safe=name in safe_tools,
            )
        )
    return tools


def _parse_call_result(value: Any, *, server_name: str) -> McpCallResult:
    metadata = {"server": server_name}
    if isinstance(value, dict):
        is_error = value.get("isError", value.get("is_error", False)) is True
        content = _stringify_mcp_content(value.get("content", value))
        if is_error:
            return McpCallResult(success=False, error=content or "MCP tool returned an error.", metadata=metadata)
        return McpCallResult(success=True, content=content, metadata=metadata)
    return McpCallResult(success=True, content=_stringify_mcp_content(value), metadata=metadata)


def _stringify_mcp_content(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
                parts.append(item["text"])
            elif isinstance(item, str):
                parts.append(item)
            else:
                parts.append(_to_json_text(item))
        return "\n".join(part for part in parts if part)
    return _to_json_text(value)


def _to_json_text(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


