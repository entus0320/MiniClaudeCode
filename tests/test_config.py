import tempfile
import unittest
from pathlib import Path

from miniclaudecode.config import ConfigError, load_config, parse_app_config


class ConfigTests(unittest.TestCase):
    def test_missing_config_file_mentions_example(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.yaml"
            env_path = Path(directory) / ".env"

            with self.assertRaises(ConfigError) as context:
                load_config(config_path=config_path, env_path=env_path)

        message = str(context.exception)
        self.assertIn("config.yaml", message)
        self.assertIn("config.example.yaml", message)

    def test_unknown_active_provider_mentions_name(self) -> None:
        raw = {
            "active_provider": "missing",
            "providers": {
                "anthropic": {
                    "protocol": "anthropic",
                    "model": "claude-opus-4-8",
                    "credential_env": "ANTHROPIC_API_KEY",
                }
            },
        }

        with self.assertRaisesRegex(ConfigError, "missing"):
            parse_app_config(raw, environ={"ANTHROPIC_API_KEY": "test-secret"})

    def test_unknown_protocol_lists_supported_protocols(self) -> None:
        raw = {
            "active_provider": "bad",
            "providers": {
                "bad": {
                    "protocol": "other",
                    "model": "model",
                    "credential_env": "TOKEN",
                }
            },
        }

        with self.assertRaises(ConfigError) as context:
            parse_app_config(raw, environ={"TOKEN": "test-secret"})

        message = str(context.exception)
        self.assertIn("Supported protocols", message)
        self.assertIn("anthropic", message)
        self.assertIn("openai-compatible", message)

    def test_missing_model_is_clear(self) -> None:
        raw = {
            "active_provider": "openai",
            "providers": {
                "openai": {
                    "protocol": "openai",
                    "credential_env": "OPENAI_API_KEY",
                }
            },
        }

        with self.assertRaisesRegex(ConfigError, "providers.openai.model"):
            parse_app_config(raw, environ={"OPENAI_API_KEY": "test-secret"})

    def test_openai_compatible_requires_base_url(self) -> None:
        raw = {
            "active_provider": "compatible",
            "providers": {
                "compatible": {
                    "protocol": "openai-compatible",
                    "model": "model",
                    "credential_env": "OPENAI_API_KEY",
                }
            },
        }

        with self.assertRaisesRegex(ConfigError, "base_url"):
            parse_app_config(raw, environ={"OPENAI_API_KEY": "test-secret"})

    def test_missing_credential_env_mentions_variable_without_secret(self) -> None:
        raw = {
            "active_provider": "anthropic",
            "providers": {
                "anthropic": {
                    "protocol": "anthropic",
                    "model": "claude-opus-4-8",
                    "credential_env": "ANTHROPIC_API_KEY",
                }
            },
        }

        with self.assertRaises(ConfigError) as context:
            parse_app_config(raw, environ={"OTHER_KEY": "test-secret"})

        message = str(context.exception)
        self.assertIn("ANTHROPIC_API_KEY", message)
        self.assertNotIn("test-secret", message)

    def test_inactive_provider_does_not_require_credential(self) -> None:
        raw = {
            "active_provider": "anthropic",
            "providers": {
                "anthropic": {
                    "protocol": "anthropic",
                    "model": "claude-opus-4-8",
                    "credential_env": "ANTHROPIC_API_KEY",
                },
                "openai": {
                    "protocol": "openai",
                    "model": "gpt-5",
                    "credential_env": "OPENAI_API_KEY",
                },
            },
        }

        app_config = parse_app_config(raw, environ={"ANTHROPIC_API_KEY": "test-secret"})

        self.assertEqual(app_config.active_provider.name, "anthropic")
        self.assertIsNone(app_config.providers["openai"].credential)

    def test_context_management_defaults_are_available(self) -> None:
        raw = {
            "active_provider": "anthropic",
            "providers": {
                "anthropic": {
                    "protocol": "anthropic",
                    "model": "claude-opus-4-8",
                    "credential_env": "ANTHROPIC_API_KEY",
                }
            },
        }

        app_config = parse_app_config(raw, environ={"ANTHROPIC_API_KEY": "test-secret"})

        self.assertTrue(app_config.context_management.enabled)
        self.assertEqual(app_config.context_management.single_tool_result_token_limit, 8000)
        self.assertEqual(app_config.context_management.tool_result_batch_token_limit, 24000)
        self.assertIsNone(app_config.context_management.context_window_tokens)
        self.assertEqual(app_config.context_management.fallback_context_window_tokens, 128000)

    def test_parses_context_management_config(self) -> None:
        raw = {
            "active_provider": "anthropic",
            "providers": {
                "anthropic": {
                    "protocol": "anthropic",
                    "model": "claude-opus-4-8",
                    "credential_env": "ANTHROPIC_API_KEY",
                }
            },
            "context_management": {
                "enabled": False,
                "artifact_dir": ".mini/context",
                "single_tool_result_token_limit": 10,
                "tool_result_batch_token_limit": 20,
                "tool_result_preview_chars": 30,
                "compact_trigger_ratio": 0.7,
                "compact_target_ratio": 0.4,
                "context_window_tokens": 1000,
                "fallback_context_window_tokens": 2000,
                "recent_message_groups_to_keep": 2,
                "max_auto_compact_failures": 4,
            },
        }

        app_config = parse_app_config(raw, environ={"ANTHROPIC_API_KEY": "test-secret"})

        context = app_config.context_management
        self.assertFalse(context.enabled)
        self.assertEqual(context.artifact_dir.as_posix(), ".mini/context")
        self.assertEqual(context.single_tool_result_token_limit, 10)
        self.assertEqual(context.tool_result_batch_token_limit, 20)
        self.assertEqual(context.tool_result_preview_chars, 30)
        self.assertEqual(context.compact_trigger_ratio, 0.7)
        self.assertEqual(context.compact_target_ratio, 0.4)
        self.assertEqual(context.context_window_tokens, 1000)
        self.assertEqual(context.fallback_context_window_tokens, 2000)
        self.assertEqual(context.recent_message_groups_to_keep, 2)
        self.assertEqual(context.max_auto_compact_failures, 4)

    def test_context_management_rejects_invalid_ratios(self) -> None:
        raw = {
            "active_provider": "anthropic",
            "providers": {
                "anthropic": {
                    "protocol": "anthropic",
                    "model": "claude-opus-4-8",
                    "credential_env": "ANTHROPIC_API_KEY",
                }
            },
            "context_management": {"compact_trigger_ratio": 0.4, "compact_target_ratio": 0.4},
        }

        with self.assertRaisesRegex(ConfigError, "compact_target_ratio"):
            parse_app_config(raw, environ={"ANTHROPIC_API_KEY": "test-secret"})

    def test_context_management_rejects_invalid_fallback_window(self) -> None:
        raw = {
            "active_provider": "anthropic",
            "providers": {
                "anthropic": {
                    "protocol": "anthropic",
                    "model": "claude-opus-4-8",
                    "credential_env": "ANTHROPIC_API_KEY",
                }
            },
            "context_management": {"fallback_context_window_tokens": 0},
        }

        with self.assertRaisesRegex(ConfigError, "fallback_context_window_tokens"):
            parse_app_config(raw, environ={"ANTHROPIC_API_KEY": "test-secret"})

    def test_mcp_servers_default_to_empty(self) -> None:
        raw = {
            "active_provider": "anthropic",
            "providers": {
                "anthropic": {
                    "protocol": "anthropic",
                    "model": "claude-opus-4-8",
                    "credential_env": "ANTHROPIC_API_KEY",
                }
            },
        }

        app_config = parse_app_config(raw, environ={"ANTHROPIC_API_KEY": "test-secret"})

        self.assertEqual(app_config.mcp_servers, ())

    def test_parses_mcp_stdio_and_http_servers(self) -> None:
        raw = {
            "active_provider": "anthropic",
            "providers": {
                "anthropic": {
                    "protocol": "anthropic",
                    "model": "claude-opus-4-8",
                    "credential_env": "ANTHROPIC_API_KEY",
                }
            },
            "mcp_servers": [
                {
                    "name": "local_tools",
                    "transport": "stdio",
                    "command": "python",
                    "args": ["server.py"],
                    "env": {"MODE": "test"},
                    "env_from": {"TOKEN": "SERVICE_TOKEN"},
                    "timeout_seconds": 3,
                    "max_reconnect_attempts": 2,
                    "safe_tools": ["echo"],
                    "install_command": "uvx fake-server",
                    "documentation_url": "https://docs.example.com/fake",
                },
                {
                    "name": "remote",
                    "transport": "streamable_http",
                    "url": "https://tools.example.com/rpc",
                    "headers_env": {"Authorization": "AUTH_HEADER"},
                },
            ],
        }

        app_config = parse_app_config(
            raw,
            environ={
                "ANTHROPIC_API_KEY": "test-secret",
                "SERVICE_TOKEN": "service-secret",
                "AUTH_HEADER": "Bearer remote-secret",
            },
        )

        local, remote = app_config.mcp_servers
        self.assertEqual(local.name, "local_tools")
        self.assertEqual(local.command, "python")
        self.assertEqual(local.args, ("server.py",))
        self.assertEqual(local.resolved_env["TOKEN"], "service-secret")
        self.assertEqual(local.safe_tools, frozenset({"echo"}))
        self.assertEqual(local.timeout_seconds, 3.0)
        self.assertEqual(local.max_reconnect_attempts, 2)
        self.assertEqual(local.install_command, "uvx fake-server")
        self.assertEqual(local.documentation_url, "https://docs.example.com/fake")
        self.assertEqual(remote.url, "https://tools.example.com/rpc")
        self.assertEqual(remote.resolved_headers["Authorization"], "Bearer remote-secret")

    def test_mcp_server_validation_errors_are_clear(self) -> None:
        raw = {
            "active_provider": "anthropic",
            "providers": {
                "anthropic": {
                    "protocol": "anthropic",
                    "model": "claude-opus-4-8",
                    "credential_env": "ANTHROPIC_API_KEY",
                }
            },
            "mcp_servers": [
                {"name": "bad-name", "transport": "stdio", "command": "python"}
            ],
        }

        with self.assertRaisesRegex(ConfigError, "mcp_servers"):
            parse_app_config(raw, environ={"ANTHROPIC_API_KEY": "test-secret"})

    def test_external_servers_is_deprecated_compatibility_alias(self) -> None:
        raw = {
            "active_provider": "anthropic",
            "providers": {
                "anthropic": {
                    "protocol": "anthropic",
                    "model": "claude-opus-4-8",
                    "credential_env": "ANTHROPIC_API_KEY",
                }
            },
            "external_servers": [{"name": "legacy", "transport": "stdio", "command": "python"}],
        }

        app_config = parse_app_config(raw, environ={"ANTHROPIC_API_KEY": "test-secret"})

        self.assertEqual(app_config.mcp_servers[0].name, "legacy")

    def test_mcp_and_external_servers_cannot_be_combined(self) -> None:
        raw = {
            "active_provider": "anthropic",
            "providers": {
                "anthropic": {
                    "protocol": "anthropic",
                    "model": "claude-opus-4-8",
                    "credential_env": "ANTHROPIC_API_KEY",
                }
            },
            "mcp_servers": [],
            "external_servers": [],
        }

        with self.assertRaisesRegex(ConfigError, "external_servers"):
            parse_app_config(raw, environ={"ANTHROPIC_API_KEY": "test-secret"})


if __name__ == "__main__":
    unittest.main()
