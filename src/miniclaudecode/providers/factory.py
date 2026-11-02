"""Provider factory for MiniClaudeCode."""

from __future__ import annotations

from miniclaudecode.config import ConfigError, ProviderConfig, SUPPORTED_PROTOCOLS
from miniclaudecode.providers.anthropic import AnthropicProvider
from miniclaudecode.providers.base import BaseProvider
from miniclaudecode.providers.openai import OpenAIProvider


def create_provider(config: ProviderConfig) -> BaseProvider:
    """Create a provider implementation for the configured protocol."""

    if config.protocol == "anthropic":
        return AnthropicProvider(config)
    if config.protocol in {"openai", "openai-compatible"}:
        return OpenAIProvider(config)

    supported = ", ".join(sorted(SUPPORTED_PROTOCOLS))
    raise ConfigError(
        f"Provider '{config.name}' uses unsupported protocol '{config.protocol}'. "
        f"Supported protocols: {supported}."
    )
