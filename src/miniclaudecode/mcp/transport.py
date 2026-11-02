"""Transport implementations for MCP JSON-RPC tool servers."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from typing import Any

from miniclaudecode.config import McpServerConfig
from miniclaudecode.mcp.jsonrpc import (
    JsonRpcNotification,
    JsonRpcProtocolError,
    JsonRpcResponse,
    RequestIdGenerator,
    make_request,
    parse_message,
    serialize_message,
)


class TransportError(Exception):
    """Raised when an MCP server transport fails."""


class TransportClosed(TransportError):
    """Raised when a transport is closed or broken."""


class TransportTimeout(TransportError):
    """Raised when a transport request times out."""


class Transport(ABC):
    """Synchronous JSON-RPC transport interface."""

    def __init__(self, config: McpServerConfig) -> None:
        self.config = config
        self._ids = RequestIdGenerator()
        self._broken = False
        self._send_lock = threading.Lock()

    @property
    def broken(self) -> bool:
        return self._broken

    def mark_broken(self) -> None:
        self._broken = True

    def _next_request_id(self) -> int:
        with self._send_lock:
            return self._ids.next()

    @abstractmethod
    def open(self) -> None:
        """Open the transport."""

    @abstractmethod
    def request(self, method: str, params: Any | None = None) -> Any:
        """Send a JSON-RPC request and return the result."""

    @abstractmethod
    def close(self) -> None:
        """Close the transport."""


class StdioTransport(Transport):
    """Line-delimited JSON-RPC over a local subprocess stdin/stdout."""

    def __init__(self, config: McpServerConfig) -> None:
        super().__init__(config)
        self._process: subprocess.Popen[str] | None = None
        self._pending: dict[int | str, queue.Queue[JsonRpcResponse | Exception]] = {}
        self._lock = threading.Lock()
        self._reader_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None

    def open(self) -> None:
        if self.config.command is None:
            raise TransportError("stdio transport requires a command.")
        env = os.environ.copy()
        env.update(self.config.env)
        env.update(self.config.resolved_env)
        try:
            self._process = subprocess.Popen(
                [self.config.command, *self.config.args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                cwd=None,
                env=env,
            )
        except OSError as exc:
            self.mark_broken()
            raise TransportError(f"stdio MCP server failed to start: {exc}") from exc
        self._broken = False
        self._reader_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader_thread.start()
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()

    def request(self, method: str, params: Any | None = None) -> Any:
        if self._process is None or self._process.stdin is None:
            raise TransportClosed("stdio transport is not open.")
        if self.broken or self._process.poll() is not None:
            self.mark_broken()
            raise TransportClosed("stdio transport is closed.")

        with self._send_lock:
            request_id = self._ids.next()
            response_queue: queue.Queue[JsonRpcResponse | Exception] = queue.Queue(maxsize=1)
            with self._lock:
                self._pending[request_id] = response_queue
            try:
                line = serialize_message(make_request(method, params, request_id)) + "\n"
                try:
                    self._process.stdin.write(line)
                    self._process.stdin.flush()
                except (BrokenPipeError, OSError) as exc:
                    self._fail_all_pending(TransportClosed("stdio transport pipe is closed."))
                    raise TransportClosed("stdio transport pipe is closed.") from exc
            except Exception:
                with self._lock:
                    self._pending.pop(request_id, None)
                raise
        try:
            try:
                item = response_queue.get(timeout=self.config.timeout_seconds)
            except queue.Empty as exc:
                self.mark_broken()
                raise TransportTimeout(f"stdio request '{method}' timed out.") from exc
            if isinstance(item, Exception):
                raise item
            item.raise_for_error()
            return item.result
        finally:
            with self._lock:
                self._pending.pop(request_id, None)

    def close(self) -> None:
        self.mark_broken()
        self._fail_all_pending(TransportClosed("stdio transport closed."))
        process = self._process
        if process is None:
            return
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass

    def _read_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        try:
            for line in process.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    message = parse_message(line)
                except JsonRpcProtocolError:
                    continue
                if isinstance(message, JsonRpcNotification):
                    continue
                self._deliver_response(message)
        finally:
            self.mark_broken()
            self._fail_all_pending(TransportClosed("stdio transport closed."))

    def _drain_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        for _line in process.stderr:
            pass

    def _deliver_response(self, response: JsonRpcResponse) -> None:
        with self._lock:
            response_queue = self._pending.get(response.id)
        if response_queue is not None:
            response_queue.put(response)

    def _fail_all_pending(self, error: Exception) -> None:
        with self._lock:
            pending = list(self._pending.values())
        for response_queue in pending:
            try:
                response_queue.put_nowait(error)
            except queue.Full:
                pass


class StreamableHttpTransport(Transport):
    """Minimal JSON-RPC over HTTP transport with SSE-like response support."""

    def open(self) -> None:
        self._broken = False

    def request(self, method: str, params: Any | None = None) -> Any:
        if self.config.url is None:
            raise TransportError("streamable_http transport requires a url.")
        if self.broken:
            raise TransportClosed("streamable_http transport is closed.")

        request_id = self._next_request_id()
        body = serialize_message(make_request(method, params, request_id)).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **self.config.headers,
            **self.config.resolved_headers,
        }
        request = urllib.request.Request(self.config.url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                status = getattr(response, "status", 200)
                if status < 200 or status >= 300:
                    self.mark_broken()
                    raise TransportError(f"HTTP server returned status {status}.")
                result = self._read_response(response, request_id)
                result.raise_for_error()
                return result.result
        except TimeoutError as exc:
            self.mark_broken()
            raise TransportTimeout(f"HTTP request '{method}' timed out.") from exc
        except urllib.error.HTTPError as exc:
            self.mark_broken()
            raise TransportError(f"HTTP server returned status {exc.code}.") from exc
        except urllib.error.URLError as exc:
            self.mark_broken()
            raise TransportClosed(f"HTTP transport failed: {exc.reason}") from exc
        except OSError as exc:
            self.mark_broken()
            raise TransportClosed(f"HTTP transport failed: {exc}") from exc

    def close(self) -> None:
        self.mark_broken()

    def _read_response(self, response: Any, request_id: int | str) -> JsonRpcResponse:
        content_type = response.headers.get("Content-Type", "")
        raw = response.read().decode("utf-8")
        if "text/event-stream" in content_type:
            return _parse_streaming_response(raw, request_id)
        return _parse_single_response(raw, request_id)


def create_transport(config: McpServerConfig) -> Transport:
    """Create a transport for one MCP server config."""

    if config.transport == "stdio":
        return StdioTransport(config)
    if config.transport == "streamable_http":
        return StreamableHttpTransport(config)
    raise TransportError(f"Unsupported MCP transport '{config.transport}'.")


def _parse_single_response(raw: str, request_id: int | str) -> JsonRpcResponse:
    message = parse_message(raw)
    if isinstance(message, JsonRpcNotification):
        raise TransportError("HTTP response was a JSON-RPC notification, not a response.")
    if message.id != request_id:
        raise TransportError("HTTP response id did not match the request id.")
    return message


def _parse_streaming_response(raw: str, request_id: int | str) -> JsonRpcResponse:
    for candidate in _iter_stream_candidates(raw):
        try:
            message = parse_message(candidate)
        except JsonRpcProtocolError:
            continue
        if isinstance(message, JsonRpcResponse) and message.id == request_id:
            return message
    raise TransportError("HTTP stream ended without a matching JSON-RPC response.")


def _iter_stream_candidates(raw: str):  # type: ignore[no-untyped-def]
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("data:"):
            stripped = stripped[5:].strip()
        if stripped == "[DONE]":
            continue
        if stripped.startswith("{"):
            yield stripped


def ensure_json_serializable(value: Any) -> None:
    """Raise TransportError if a value cannot be serialized as JSON."""

    try:
        json.dumps(value)
    except (TypeError, ValueError) as exc:
        raise TransportError("Value is not JSON serializable.") from exc
