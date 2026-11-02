import json
import sys
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from miniclaudecode.config import McpServerConfig
from miniclaudecode.mcp.transport import StdioTransport, TransportClosed


FAKE_SERVER = r'''
import json
import sys
for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    if method == "initialize":
        result = {"ok": True}
    elif method == "tools/list":
        result = {"tools": [{"name": "echo", "description": "Echo", "inputSchema": {"type": "object", "properties": {}}}]}
    elif method == "tools/call":
        result = {"content": request["params"]["arguments"].get("text", "")}
    else:
        result = None
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
'''


class StdioTransportTests(unittest.TestCase):
    def test_stdio_transport_round_trip(self) -> None:
        with TemporaryDirectory() as directory:
            server_path = Path(directory) / "fake_server.py"
            server_path.write_text(textwrap.dedent(FAKE_SERVER), encoding="utf-8")
            transport = StdioTransport(
                McpServerConfig(
                    name="fake",
                    transport="stdio",
                    command=sys.executable,
                    args=(str(server_path),),
                    timeout_seconds=2,
                )
            )
            try:
                transport.open()
                self.assertEqual(transport.request("initialize", {}), {"ok": True})
                tools = transport.request("tools/list", {})
                self.assertEqual(tools["tools"][0]["name"], "echo")
                result = transport.request("tools/call", {"name": "echo", "arguments": {"text": "pong"}})
                self.assertEqual(result["content"], "pong")
            finally:
                transport.close()

    def test_request_after_close_fails(self) -> None:
        with TemporaryDirectory() as directory:
            server_path = Path(directory) / "fake_server.py"
            server_path.write_text(textwrap.dedent(FAKE_SERVER), encoding="utf-8")
            transport = StdioTransport(
                McpServerConfig(name="fake", transport="stdio", command=sys.executable, args=(str(server_path),))
            )
            transport.open()
            transport.close()

            with self.assertRaises(TransportClosed):
                transport.request("initialize", {})


if __name__ == "__main__":
    unittest.main()
