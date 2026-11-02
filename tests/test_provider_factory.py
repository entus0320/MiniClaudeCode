import unittest

from miniclaudecode.config import ConfigError, ProviderConfig
from miniclaudecode.providers.anthropic import AnthropicProvider
from miniclaudecode.providers.factory import create_provider
from miniclaudecode.providers.openai import OpenAIProvider


class ProviderFactoryTests(unittest.TestCase):
    def test_creates_anthropic_provider(self) -> None:
        provider = create_provider(
            ProviderConfig(
                name="anthropic",
                protocol="anthropic",
                model="claude-opus-4-8",
                credential_env="ANTHROPIC_API_KEY",
                credential="test-secret",
            )
        )

        self.assertIsInstance(provider, AnthropicProvider)

    def test_creates_openai_provider(self) -> None:
        provider = create_provider(
            ProviderConfig(
                name="openai",
                protocol="openai",
                model="gpt-5",
                credential_env="OPENAI_API_KEY",
                credential="test-secret",
            )
        )

        self.assertIsInstance(provider, OpenAIProvider)

    def test_creates_openai_compatible_provider(self) -> None:
        provider = create_provider(
            ProviderConfig(
                name="compatible",
                protocol="openai-compatible",
                model="model",
                credential_env="OPENAI_API_KEY",
                credential="test-secret",
                base_url="https://api.example.com/v1",
            )
        )

        self.assertIsInstance(provider, OpenAIProvider)

    def test_unknown_protocol_raises_config_error(self) -> None:
        config = ProviderConfig(
            name="bad",
            protocol="bad-protocol",
            model="model",
            credential_env="TOKEN",
            credential="test-secret",
        )

        with self.assertRaisesRegex(ConfigError, "Supported protocols"):
            create_provider(config)


if __name__ == "__main__":
    unittest.main()
