import unittest
from typing import Any

from miniclaudecode.config import McpServerConfig
from miniclaudecode.mcp.client import ConnectionState, McpServerConnection
from miniclaudecode.mcp.transport import Transport


class FakeTransport(Transport):
    def __init__(self, config: McpServerConfig) -> None:
        super().__init__(config)
        self.requests: list[tuple[str, Any]] = []

    def open(self) -> None:
        self._broken = False

    def request(self, method: str, params: Any | None = None) -> Any:
        self.requests.append((method, params))
        if method == "initialize":
            return {"ok": True}
        if method == "tools/list":
            return {
                "tools": [
                    {
                        "name": "echo",
                        "description": "Echo text.",
                        "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
                    }
                ]
            }
        if method == "tools/call":
            return {"content": [{"type": "text", "text": params["arguments"]["text"]}]}
        raise AssertionError(method)

    def close(self) -> None:
        self.mark_broken()


class McpServerConnectionTests(unittest.TestCase):
    def test_connect_initializes_and_lists_tools(self) -> None:
        transports: list[FakeTransport] = []

        def factory(config: McpServerConfig) -> FakeTransport:
            transport = FakeTransport(config)
            transports.append(transport)
            return transport

        connection = McpServerConnection(
            McpServerConfig(name="fake", transport="stdio", command="python"),
            transport_factory=factory,
        )

        connection.connect()

        self.assertEqual(connection.state, ConnectionState.READY)
        self.assertEqual([tool.name for tool in connection.tools], ["echo"])
        self.assertEqual([method for method, _ in transports[0].requests], ["initialize", "tools/list"])

    def test_call_tool_normalizes_result(self) -> None:
        connection = McpServerConnection(
            McpServerConfig(name="fake", transport="stdio", command="python"),
            transport_factory=FakeTransport,
        )
        connection.connect()

        result = connection.call_tool("echo", {"text": "pong"})

        self.assertTrue(result.success)
        self.assertEqual(result.content, "pong")


if __name__ == "__main__":
    unittest.main()
