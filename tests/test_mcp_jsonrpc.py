import unittest

from miniclaudecode.mcp.jsonrpc import (
    JsonRpcNotification,
    JsonRpcProtocolError,
    JsonRpcRemoteError,
    JsonRpcResponse,
    RequestIdGenerator,
    make_request,
    parse_message,
    serialize_message,
)


class JsonRpcTests(unittest.TestCase):
    def test_make_request_and_serialize_message(self) -> None:
        request = make_request("tools/list", {}, 1)

        self.assertEqual(request, {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        self.assertEqual(serialize_message(request), '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}')

    def test_parse_result_response(self) -> None:
        message = parse_message('{"jsonrpc":"2.0","id":2,"result":{"ok":true}}')

        self.assertEqual(message, JsonRpcResponse(id=2, result={"ok": True}))

    def test_parse_error_response(self) -> None:
        message = parse_message('{"jsonrpc":"2.0","id":"abc","error":{"code":-32602,"message":"bad params"}}')

        self.assertIsInstance(message, JsonRpcResponse)
        self.assertIsInstance(message.error, JsonRpcRemoteError)
        with self.assertRaisesRegex(JsonRpcRemoteError, "bad params"):
            message.raise_for_error()

    def test_parse_notification(self) -> None:
        message = parse_message('{"jsonrpc":"2.0","method":"notifications/progress","params":{"n":1}}')

        self.assertEqual(message, JsonRpcNotification(method="notifications/progress", params={"n": 1}))

    def test_malformed_json_raises_protocol_error(self) -> None:
        with self.assertRaises(JsonRpcProtocolError):
            parse_message("not-json")

    def test_request_id_generator_is_monotonic(self) -> None:
        generator = RequestIdGenerator()

        self.assertEqual([generator.next(), generator.next(), generator.next()], [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
