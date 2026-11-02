import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from miniclaudecode.config import McpServerConfig
from miniclaudecode.mcp.transport import StreamableHttpTransport, TransportError


class JsonRpcHandler(BaseHTTPRequestHandler):
    mode = "json"
    received_headers: dict[str, str] = {}

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        request = json.loads(body)
        JsonRpcHandler.received_headers = dict(self.headers.items())
        if JsonRpcHandler.mode == "error":
            self.send_response(500)
            self.end_headers()
            return
        payload = {"jsonrpc": "2.0", "id": request["id"], "result": {"ok": request["method"]}}
        if JsonRpcHandler.mode == "sse":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(("data: " + json.dumps(payload) + "\n\n").encode("utf-8"))
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode("utf-8"))

    def log_message(self, format, *args):  # type: ignore[no-untyped-def]
        return


class HttpTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        JsonRpcHandler.mode = "json"
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), JsonRpcHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.url = f"http://{host}:{port}/rpc"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_http_json_response(self) -> None:
        transport = StreamableHttpTransport(
            McpServerConfig(
                name="remote",
                transport="streamable_http",
                url=self.url,
                headers={"X-Test": "yes"},
                timeout_seconds=2,
            )
        )
        transport.open()

        result = transport.request("initialize", {})

        self.assertEqual(result, {"ok": "initialize"})
        self.assertEqual(JsonRpcHandler.received_headers["X-Test"], "yes")

    def test_http_sse_response(self) -> None:
        JsonRpcHandler.mode = "sse"
        transport = StreamableHttpTransport(
            McpServerConfig(name="remote", transport="streamable_http", url=self.url, timeout_seconds=2)
        )
        transport.open()

        result = transport.request("tools/list", {})

        self.assertEqual(result, {"ok": "tools/list"})

    def test_http_error_marks_broken(self) -> None:
        JsonRpcHandler.mode = "error"
        transport = StreamableHttpTransport(
            McpServerConfig(name="remote", transport="streamable_http", url=self.url, timeout_seconds=2)
        )
        transport.open()

        with self.assertRaises(TransportError):
            transport.request("initialize", {})
        self.assertTrue(transport.broken)


if __name__ == "__main__":
    unittest.main()
