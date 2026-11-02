import unittest
from pathlib import Path
from types import SimpleNamespace

from miniclaudecode.config import ProviderConfig
from miniclaudecode.prompt_modules import default_prompt_modules
from miniclaudecode.prompting import DynamicInstruction, PromptAssembler, RuntimeContext
from miniclaudecode.providers.base import ProviderError, ProviderTurnComplete
from miniclaudecode.providers.openai import OpenAIProvider, to_openai_messages
from miniclaudecode.session import Message
from miniclaudecode.tools.read_file import ReadFileTool


class FakeCompletions:
    def __init__(self, chunks, token_count_result=None) -> None:  # type: ignore[no-untyped-def]
        self._chunks = chunks
        self._token_count_result = token_count_result
        self.last_request = None
        self.last_count_request = None

    def create(self, **kwargs):  # type: ignore[no-untyped-def]
        self.last_request = kwargs
        if isinstance(self._chunks, Exception):
            raise self._chunks
        return iter(self._chunks)

    def count_tokens(self, **kwargs):  # type: ignore[no-untyped-def]
        self.last_count_request = kwargs
        if isinstance(self._token_count_result, Exception):
            raise self._token_count_result
        if self._token_count_result is None:
            raise AttributeError("count_tokens is unavailable")
        return self._token_count_result


class FakeChat:
    def __init__(self, chunks, token_count_result=None) -> None:  # type: ignore[no-untyped-def]
        self.completions = FakeCompletions(chunks, token_count_result=token_count_result)


class FakeClient:
    def __init__(self, chunks, token_count_result=None) -> None:  # type: ignore[no-untyped-def]
        self.chat = FakeChat(chunks, token_count_result=token_count_result)


def chunk(content: str | None):
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=content))]
    )


def usage_chunk(*, prompt_tokens: int, completion_tokens: int):
    return SimpleNamespace(
        choices=[],
        usage=SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
    )


def tool_chunk(index: int, *, call_id: str | None = None, name: str | None = None, arguments: str | None = None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            index=index,
                            id=call_id,
                            function=SimpleNamespace(name=name, arguments=arguments),
                        )
                    ],
                )
            )
        ]
    )


class OpenAIProviderTests(unittest.TestCase):
    def test_converts_messages(self) -> None:
        messages = [
            Message(role="user", content="hello"),
            Message(role="assistant", content="hi"),
        ]

        self.assertEqual(
            to_openai_messages(messages),
            [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
            ],
        )

    def test_stream_chat_yields_delta_content(self) -> None:
        config = ProviderConfig(
            name="openai",
            protocol="openai",
            model="gpt-5",
            credential_env="OPENAI_API_KEY",
            credential="test-secret",
        )
        client = FakeClient([chunk("po"), chunk(None), chunk("ng")])
        provider = OpenAIProvider(config, client=client)

        self.assertEqual(list(provider.stream_chat([Message(role="user", content="ping")])), ["po", "ng"])
        self.assertEqual(client.chat.completions.last_request["model"], "gpt-5")
        self.assertTrue(client.chat.completions.last_request["stream"])
        self.assertEqual(
            client.chat.completions.last_request["messages"],
            [{"role": "user", "content": "ping"}],
        )

    def test_openai_compatible_preserves_base_url_config(self) -> None:
        config = ProviderConfig(
            name="openai-compatible",
            protocol="openai-compatible",
            model="compatible-model",
            credential_env="OPENAI_API_KEY",
            credential="test-secret",
            base_url="https://api.example.com/v1",
        )
        provider = OpenAIProvider(config, client=FakeClient([chunk("ok")]))

        self.assertEqual(provider.config.base_url, "https://api.example.com/v1")
        self.assertEqual(list(provider.stream_chat([Message(role="user", content="hi")])), ["ok"])

    def test_stream_turn_sends_tools_and_parses_tool_call_chunks(self) -> None:
        config = ProviderConfig(
            name="openai",
            protocol="openai",
            model="gpt-5",
            credential_env="OPENAI_API_KEY",
            credential="test-secret",
        )
        client = FakeClient([
            tool_chunk(0, call_id="call_1", name="read_file", arguments="{\"path\":"),
            tool_chunk(0, arguments="\"README.md\"}"),
        ])
        provider = OpenAIProvider(config, client=client)

        events = list(provider.stream_turn([Message(role="user", content="read")], tools=[ReadFileTool()]))

        tool = client.chat.completions.last_request["tools"][0]["function"]
        self.assertEqual(tool["name"], "read_file")
        self.assertEqual(tool["description"], ReadFileTool().description)
        self.assertEqual(tool["parameters"], ReadFileTool().input_schema)
        complete = events[-1]
        self.assertIsInstance(complete, ProviderTurnComplete)
        self.assertEqual(complete.tool_calls[0].id, "call_1")
        self.assertEqual(complete.tool_calls[0].arguments, {"path": "README.md"})

    def test_stream_turn_prepends_prompt_as_system_message(self) -> None:
        config = ProviderConfig(
            name="openai",
            protocol="openai",
            model="gpt-5",
            credential_env="OPENAI_API_KEY",
            credential="test-secret",
        )
        client = FakeClient([chunk("ok")])
        provider = OpenAIProvider(config, client=client)
        prompt = PromptAssembler(default_prompt_modules()).assemble(
            runtime_context=RuntimeContext(
                workdir=Path("/tmp/project"),
                os_name="test-os",
                current_date="2026-06-14",
                provider_name="openai",
                model="gpt-5",
            ),
            dynamic_instructions=[DynamicInstruction(name="notice", content="只读。")],
        )

        list(provider.stream_turn([Message(role="user", content="hello")], tools=[ReadFileTool()], prompt=prompt))

        request = client.chat.completions.last_request
        messages = request["messages"]
        self.assertEqual(messages[0]["role"], "system")
        system_content = messages[0]["content"]
        self.assertIn("[identity]", system_content)
        self.assertIn("Working directory", system_content)
        self.assertIn("<system-reminder>", system_content)
        self.assertLess(system_content.index("[identity]"), system_content.index("Working directory"))
        self.assertEqual(messages[1], {"role": "user", "content": "hello"})
        self.assertNotIn(ReadFileTool().description, system_content)
        self.assertEqual(request["tools"][0]["function"]["description"], ReadFileTool().description)
        self.assertNotIn("cache_control", str(request))
        self.assertNotIn("system", request)

    def test_stream_turn_parses_usage_for_openai(self) -> None:
        config = ProviderConfig(
            name="openai",
            protocol="openai",
            model="gpt-5",
            credential_env="OPENAI_API_KEY",
            credential="test-secret",
        )
        provider = OpenAIProvider(config, client=FakeClient([chunk("ok"), usage_chunk(prompt_tokens=10, completion_tokens=2)]))

        complete = list(provider.stream_turn([Message(role="user", content="hi")]))[-1]

        self.assertIsInstance(complete, ProviderTurnComplete)
        self.assertIsNotNone(complete.usage)
        self.assertEqual(complete.usage.input_tokens, 10)
        self.assertEqual(complete.usage.output_tokens, 2)

    def test_openai_context_window_is_known_for_default_model(self) -> None:
        config = ProviderConfig(
            name="openai",
            protocol="openai",
            model="gpt-5",
            credential_env="OPENAI_API_KEY",
            credential="test-secret",
        )
        provider = OpenAIProvider(config, client=FakeClient([chunk("ok")]))

        self.assertEqual(provider.context_window_tokens, 400000)

    def test_count_tokens_uses_client_counter_when_available(self) -> None:
        config = ProviderConfig(
            name="openai",
            protocol="openai",
            model="gpt-5",
            credential_env="OPENAI_API_KEY",
            credential="test-secret",
        )
        client = FakeClient([chunk("ok")], token_count_result=SimpleNamespace(prompt_tokens=123))
        provider = OpenAIProvider(config, client=client)
        tool = ReadFileTool()

        tokens = provider.count_tokens([Message(role="user", content="hi")], tools=[tool])

        self.assertEqual(tokens, 123)
        self.assertNotIn("stream", client.chat.completions.last_count_request)
        self.assertNotIn("stream_options", client.chat.completions.last_count_request)
        self.assertEqual(client.chat.completions.last_count_request["model"], "gpt-5")
        self.assertEqual(client.chat.completions.last_count_request["messages"], [{"role": "user", "content": "hi"}])
        self.assertEqual(client.chat.completions.last_count_request["tools"][0]["function"]["name"], "read_file")

    def test_count_tokens_is_not_available_for_openai_compatible(self) -> None:
        config = ProviderConfig(
            name="openai-compatible",
            protocol="openai-compatible",
            model="compatible-model",
            credential_env="OPENAI_API_KEY",
            credential="test-secret",
            base_url="https://api.example.com/v1",
        )
        provider = OpenAIProvider(config, client=FakeClient([chunk("ok")], token_count_result=SimpleNamespace(prompt_tokens=123)))

        self.assertEqual(provider.count_tokens([Message(role="user", content="hi")]), 123)
        self.assertIsNone(provider.context_window_tokens)

    def test_provider_error_does_not_leak_secret(self) -> None:
        config = ProviderConfig(
            name="openai",
            protocol="openai",
            model="gpt-5",
            credential_env="OPENAI_API_KEY",
            credential="test-secret",
        )
        provider = OpenAIProvider(config, client=FakeClient(RuntimeError("boom test-secret")))

        with self.assertRaises(ProviderError) as context:
            list(provider.stream_chat([Message(role="user", content="hello")]))

        self.assertNotIn("test-secret", str(context.exception))
        self.assertIn("OpenAI provider", str(context.exception))


if __name__ == "__main__":
    unittest.main()
