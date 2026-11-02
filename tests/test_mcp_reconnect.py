import unittest
from typing import Any

from miniclaudecode.config import McpServerConfig
from miniclaudecode.mcp.client import McpServerConnection
from miniclaudecode.mcp.transport import Transport, TransportClosed


class FlakyTransport(Transport):
    instances: list["FlakyTransport"] = []
    fail_method: str | None = None
    fail_once = True

    def __init__(self, config: McpServerConfig) -> None:
        super().__init__(config)
        self.requests: list[tuple[str, Any]] = []
        FlakyTransport.instances.append(self)

    def open(self) -> None:
        self._broken = False

    def request(self, method: str, params: Any | None = None) -> Any:
        self.requests.append((method, params))
        if FlakyTransport.fail_once and method == FlakyTransport.fail_method:
            FlakyTransport.fail_once = False
            self.mark_broken()
            raise TransportClosed("boom")
        if method == "initialize":
            return {}
        if method == "tools/list":
            return {"tools": [{"name": "echo", "description": "Echo", "inputSchema": {"type": "object", "properties": {}}}]}
        if method == "tools/call":
            return {"content": "ok"}
        raise AssertionError(method)

    def close(self) -> None:
        self.mark_broken()


class ReconnectTests(unittest.TestCase):
    def setUp(self) -> None:
        FlakyTransport.instances = []
        FlakyTransport.fail_method = None
        FlakyTransport.fail_once = True

    def test_tools_list_disconnect_recovers(self) -> None:
        FlakyTransport.fail_method = "tools/list"
        connection = McpServerConnection(
            McpServerConfig(name="fake", transport="stdio", command="python", max_reconnect_attempts=1),
            transport_factory=FlakyTransport,
        )

        connection.connect()

        self.assertEqual(len(FlakyTransport.instances), 2)
        self.assertEqual([tool.name for tool in connection.tools], ["echo"])

    def test_safe_tool_call_disconnect_retries(self) -> None:
        connection = McpServerConnection(
            McpServerConfig(
                name="fake",
                transport="stdio",
                command="python",
                max_reconnect_attempts=1,
                safe_tools=frozenset({"echo"}),
            ),
            transport_factory=FlakyTransport,
        )
        connection.connect()
        FlakyTransport.fail_method = "tools/call"
        FlakyTransport.fail_once = True

        result = connection.call_tool("echo", {})

        self.assertTrue(result.success)
        self.assertEqual(len(FlakyTransport.instances), 2)

    def test_unsafe_tool_call_disconnect_does_not_retry(self) -> None:
        connection = McpServerConnection(
            McpServerConfig(name="fake", transport="stdio", command="python", max_reconnect_attempts=1),
            transport_factory=FlakyTransport,
        )
        connection.connect()
        FlakyTransport.fail_method = "tools/call"
        FlakyTransport.fail_once = True

        result = connection.call_tool("echo", {})

        self.assertFalse(result.success)
        self.assertIn("避免重复副作用", result.error or "")
        self.assertEqual(len(FlakyTransport.instances), 1)


if __name__ == "__main__":
    unittest.main()
