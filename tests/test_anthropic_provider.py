import unittest
from pathlib import Path
from types import SimpleNamespace

from miniclaudecode.config import ProviderConfig, ThinkingConfig
from miniclaudecode.prompt_modules import default_prompt_modules
from miniclaudecode.prompting import DynamicInstruction, PromptAssembler, RuntimeContext
from miniclaudecode.providers.anthropic import AnthropicProvider, to_anthropic_messages
from miniclaudecode.providers.base import ProviderError, ProviderTurnComplete
from miniclaudecode.session import Message
from miniclaudecode.tools.base import ToolCall
from miniclaudecode.tools.defaults import create_default_tool_registry
from miniclaudecode.tools.read_file import ReadFileTool


class FakeAnthropicStream:
    def __init__(self, parts: list[str], final_message=None) -> None:  # type: ignore[no-untyped-def]
        self.text_stream = iter(parts)
        self._final_message = final_message

    def __enter__(self) -> "FakeAnthropicStream":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:  # type: ignore[no-untyped-def]
        return None

    def get_final_message(self):  # type: ignore[no-untyped-def]
        return self._final_message


class FailingAnthropicStream:
    def __enter__(self) -> "FailingAnthropicStream":
        raise RuntimeError("boom test-secret")

    def __exit__(self, exc_type, exc, traceback) -> None:  # type: ignore[no-untyped-def]
        return None


class FakeMessages:
    def __init__(self, stream) -> None:  # type: ignore[no-untyped-def]
        self._stream = stream
        self.last_request = None
        self.last_count_request = None

    def stream(self, **kwargs):  # type: ignore[no-untyped-def]
        self.last_request = kwargs
        return self._stream

    def count_tokens(self, **kwargs):  # type: ignore[no-untyped-def]
        self.last_count_request = kwargs
        return SimpleNamespace(input_tokens=123)


class FakeClient:
    def __init__(self, stream) -> None:  # type: ignore[no-untyped-def]
        self.messages = FakeMessages(stream)


class AnthropicProviderTests(unittest.TestCase):
    def test_converts_messages(self) -> None:
        messages = [
            Message(role="user", content="hello"),
            Message(role="assistant", content="hi"),
        ]

        self.assertEqual(
            to_anthropic_messages(messages),
            [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
            ],
        )

    def test_stream_chat_yields_text_deltas_and_sends_history(self) -> None:
        config = ProviderConfig(
            name="anthropic",
            protocol="anthropic",
            model="claude-opus-4-8",
            credential_env="ANTHROPIC_API_KEY",
            credential="test-secret",
        )
        client = FakeClient(FakeAnthropicStream(["你", "好"]))
        provider = AnthropicProvider(config, client=client)
        messages = [Message(role="user", content="hello")]

        self.assertEqual(list(provider.stream_chat(messages)), ["你", "好"])
        self.assertEqual(
            client.messages.last_request["messages"],
            [{"role": "user", "content": "hello"}],
        )
        self.assertEqual(client.messages.last_request["model"], "claude-opus-4-8")

    def test_thinking_config_uses_adaptive_thinking(self) -> None:
        config = ProviderConfig(
            name="anthropic",
            protocol="anthropic",
            model="claude-opus-4-8",
            credential_env="ANTHROPIC_API_KEY",
            credential="test-secret",
            thinking=ThinkingConfig(enabled=True, effort="xhigh", display="omitted"),
        )
        client = FakeClient(FakeAnthropicStream(["ok"]))
        provider = AnthropicProvider(config, client=client)

        self.assertEqual(list(provider.stream_chat([Message(role="user", content="hi")])), ["ok"])

        self.assertEqual(
            client.messages.last_request["thinking"],
            {"type": "adaptive", "display": "omitted"},
        )
        self.assertEqual(client.messages.last_request["output_config"], {"effort": "xhigh"})
        self.assertNotIn("budget_tokens", client.messages.last_request)

    def test_stream_turn_sends_tools_and_parses_tool_use(self) -> None:
        config = ProviderConfig(
            name="anthropic",
            protocol="anthropic",
            model="claude-opus-4-8",
            credential_env="ANTHROPIC_API_KEY",
            credential="test-secret",
        )
        final_message = SimpleNamespace(
            content=[SimpleNamespace(type="tool_use", id="toolu_1", name="read_file", input={"path": "README.md"})],
            stop_reason="tool_use",
        )
        client = FakeClient(FakeAnthropicStream([], final_message))
        provider = AnthropicProvider(config, client=client)

        events = list(provider.stream_turn([Message(role="user", content="read")], tools=[ReadFileTool()]))

        tool = client.messages.last_request["tools"][0]
        self.assertEqual(tool["name"], "read_file")
        self.assertEqual(tool["description"], ReadFileTool().description)
        self.assertEqual(tool["input_schema"], ReadFileTool().input_schema)
        self.assertEqual(tool["cache_control"], {"type": "ephemeral"})
        complete = events[-1]
        self.assertIsInstance(complete, ProviderTurnComplete)
        self.assertEqual(complete.tool_calls[0].name, "read_file")
        self.assertEqual(complete.tool_calls[0].arguments, {"path": "README.md"})

    def test_stream_turn_sends_prompt_system_blocks_with_cache_control(self) -> None:
        config = ProviderConfig(
            name="anthropic",
            protocol="anthropic",
            model="claude-opus-4-8",
            credential_env="ANTHROPIC_API_KEY",
            credential="test-secret",
        )
        client = FakeClient(FakeAnthropicStream(["ok"]))
        provider = AnthropicProvider(config, client=client)
        prompt = PromptAssembler(default_prompt_modules()).assemble(
            runtime_context=RuntimeContext(
                workdir=Path("/tmp/project"),
                os_name="test-os",
                current_date="2026-06-14",
                provider_name="anthropic",
                model="claude-opus-4-8",
            ),
            dynamic_instructions=[DynamicInstruction(name="notice", content="只读。")],
        )

        list(provider.stream_turn([Message(role="user", content="hello")], prompt=prompt))

        system = client.messages.last_request["system"]
        self.assertIsInstance(system, list)
        self.assertIn("cache_control", system[len(prompt.stable_blocks) - 1])
        self.assertEqual(system[len(prompt.stable_blocks) - 1]["cache_control"], {"type": "ephemeral"})
        self.assertFalse(any("cache_control" in block for block in system[len(prompt.stable_blocks) :]))
        self.assertIn("[identity]", system[0]["text"])
        self.assertIn("[runtime_context]", system[len(prompt.stable_blocks)]["text"])
        self.assertIn("Working directory", system[len(prompt.stable_blocks)]["text"])
        self.assertIn("test-os", system[len(prompt.stable_blocks)]["text"])
        self.assertIn("<system-reminder>", system[-1]["text"])
        self.assertEqual(client.messages.last_request["messages"], [{"role": "user", "content": "hello"}])

    def test_stream_turn_adds_cache_control_to_last_tool_only(self) -> None:
        config = ProviderConfig(
            name="anthropic",
            protocol="anthropic",
            model="claude-opus-4-8",
            credential_env="ANTHROPIC_API_KEY",
            credential="test-secret",
        )
        client = FakeClient(FakeAnthropicStream(["ok"]))
        provider = AnthropicProvider(config, client=client)
        tools = create_default_tool_registry().list_tools()

        list(provider.stream_turn([Message(role="user", content="hello")], tools=tools))

        request_tools = client.messages.last_request["tools"]
        self.assertEqual(len(request_tools), len(tools))
        self.assertFalse(any("cache_control" in tool for tool in request_tools[:-1]))
        self.assertEqual(request_tools[-1]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(request_tools[0]["description"], tools[0].description)
        self.assertEqual(request_tools[0]["input_schema"], tools[0].input_schema)

    def test_stream_turn_partitions_system_tools_and_messages(self) -> None:
        config = ProviderConfig(
            name="anthropic",
            protocol="anthropic",
            model="claude-opus-4-8",
            credential_env="ANTHROPIC_API_KEY",
            credential="test-secret",
        )
        client = FakeClient(FakeAnthropicStream(["ok"]))
        provider = AnthropicProvider(config, client=client)
        prompt = PromptAssembler(default_prompt_modules()).assemble(
            runtime_context=RuntimeContext(
                workdir=Path("/tmp/project"),
                os_name="test-os",
                current_date="2026-06-14",
                provider_name="anthropic",
                model="claude-opus-4-8",
            ),
            dynamic_instructions=[DynamicInstruction(name="notice", content="只读。")],
        )
        messages = [Message(role="user", content="hello"), Message(role="assistant", content="hi")]
        tool = ReadFileTool()

        list(provider.stream_turn(messages, tools=[tool], prompt=prompt))

        request = client.messages.last_request
        system_text = "\n".join(block["text"] for block in request["system"])
        messages_text = str(request["messages"])
        self.assertIn("[identity]", system_text)
        self.assertIn("[runtime_context]", system_text)
        self.assertIn("<system-reminder>", system_text)
        self.assertNotIn("hello", system_text)
        self.assertEqual(request["tools"][0]["name"], tool.name)
        self.assertEqual(request["tools"][0]["description"], tool.description)
        self.assertEqual(request["tools"][0]["input_schema"], tool.input_schema)
        self.assertEqual(
            request["messages"],
            [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}],
        )
        self.assertNotIn("[identity]", messages_text)
        self.assertNotIn("Working directory", messages_text)
        self.assertNotIn("<system-reminder>", messages_text)
        self.assertNotIn(tool.description, messages_text)

    def test_converts_tool_result_and_assistant_tool_calls_to_messages(self) -> None:
        messages = [
            Message(
                role="assistant",
                content="",
                tool_calls=[ToolCall(id="call-1", name="read_file", arguments={"path": "README.md"})],
            ),
            Message(role="tool", content="result", tool_call_id="call-1", tool_name="read_file"),
        ]

        converted = to_anthropic_messages(messages)

        self.assertEqual(
            converted[0],
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "call-1",
                        "name": "read_file",
                        "input": {"path": "README.md"},
                    }
                ],
            },
        )
        self.assertEqual(
            converted[1],
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call-1",
                        "content": "result",
                    }
                ],
            },
        )

    def test_stream_turn_parses_usage(self) -> None:
        config = ProviderConfig(
            name="anthropic",
            protocol="anthropic",
            model="claude-opus-4-8",
            credential_env="ANTHROPIC_API_KEY",
            credential="test-secret",
        )
        final_message = SimpleNamespace(
            content=[SimpleNamespace(type="text", text="ok")],
            stop_reason="end_turn",
            usage=SimpleNamespace(
                input_tokens=10,
                output_tokens=2,
                cache_creation_input_tokens=100,
                cache_read_input_tokens=50,
            ),
        )
        provider = AnthropicProvider(config, client=FakeClient(FakeAnthropicStream([], final_message)))

        complete = list(provider.stream_turn([Message(role="user", content="hi")]))[-1]

        self.assertIsInstance(complete, ProviderTurnComplete)
        self.assertIsNotNone(complete.usage)
        self.assertEqual(complete.usage.input_tokens, 10)
        self.assertEqual(complete.usage.output_tokens, 2)
        self.assertEqual(complete.usage.cache_creation_input_tokens, 100)
        self.assertEqual(complete.usage.cache_read_input_tokens, 50)

    def test_stream_turn_allows_missing_usage(self) -> None:
        config = ProviderConfig(
            name="anthropic",
            protocol="anthropic",
            model="claude-opus-4-8",
            credential_env="ANTHROPIC_API_KEY",
            credential="test-secret",
        )
        final_message = SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")], stop_reason="end_turn")
        provider = AnthropicProvider(config, client=FakeClient(FakeAnthropicStream([], final_message)))

        complete = list(provider.stream_turn([Message(role="user", content="hi")]))[-1]

        self.assertIsInstance(complete, ProviderTurnComplete)
        self.assertIsNone(complete.usage)

    def test_count_tokens_uses_request_shape_without_max_tokens(self) -> None:
        config = ProviderConfig(
            name="anthropic",
            protocol="anthropic",
            model="claude-opus-4-8",
            credential_env="ANTHROPIC_API_KEY",
            credential="test-secret",
        )
        client = FakeClient(FakeAnthropicStream(["ok"]))
        provider = AnthropicProvider(config, client=client)
        tool = ReadFileTool()

        tokens = provider.count_tokens([Message(role="user", content="hi")], tools=[tool])

        self.assertEqual(tokens, 123)
        self.assertNotIn("max_tokens", client.messages.last_count_request)
        self.assertEqual(client.messages.last_count_request["model"], "claude-opus-4-8")
        self.assertEqual(client.messages.last_count_request["messages"], [{"role": "user", "content": "hi"}])
        self.assertEqual(client.messages.last_count_request["tools"][0]["name"], "read_file")

    def test_provider_error_does_not_leak_secret(self) -> None:
        config = ProviderConfig(
            name="anthropic",
            protocol="anthropic",
            model="claude-opus-4-8",
            credential_env="ANTHROPIC_API_KEY",
            credential="test-secret",
        )
        provider = AnthropicProvider(config, client=FakeClient(FailingAnthropicStream()))

        with self.assertRaises(ProviderError) as context:
            list(provider.stream_chat([Message(role="user", content="hello")]))

        self.assertNotIn("test-secret", str(context.exception))
        self.assertIn("Anthropic provider", str(context.exception))


if __name__ == "__main__":
    unittest.main()
