"""Configuration loading for MiniClaudeCode."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from miniclaudecode.context import ContextManagementConfig
from miniclaudecode.instructions import InstructionConfig
from miniclaudecode.teams.core import AgentTeamConfig, TeamBackendKind
from miniclaudecode.worktrees.core import IgnoredFileRule, WorktreeConfig

DEFAULT_CONFIG_PATH = Path("config.yaml")
DEFAULT_ENV_PATH = Path(".env")

SUPPORTED_PROTOCOLS = frozenset({"anthropic", "openai", "openai-compatible"})
SUPPORTED_THINKING_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})
SUPPORTED_THINKING_DISPLAYS = frozenset({"omitted", "summarized"})
SUPPORTED_MCP_TRANSPORTS = frozenset({"stdio", "streamable_http"})
SERVER_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ConfigError(Exception):
    """Raised when configuration cannot be loaded safely."""


@dataclass(frozen=True)
class ThinkingConfig:
    """Anthropic thinking options parsed from configuration."""

    enabled: bool = False
    effort: str = "high"
    display: str = "omitted"


@dataclass(frozen=True)
class ProviderConfig:
    """Configuration for one model provider."""

    name: str
    protocol: str
    model: str
    credential_env: str
    credential: str | None = field(default=None, repr=False)
    base_url: str | None = None
    thinking: ThinkingConfig = field(default_factory=ThinkingConfig)


@dataclass(frozen=True)
class McpServerConfig:
    """Configuration for one MCP tool server."""

    name: str
    transport: str
    command: str | None = None
    args: Sequence[str] = field(default_factory=tuple)
    url: str | None = None
    env: Mapping[str, str] = field(default_factory=dict)
    env_from: Mapping[str, str] = field(default_factory=dict)
    headers: Mapping[str, str] = field(default_factory=dict)
    headers_env: Mapping[str, str] = field(default_factory=dict)
    resolved_env: Mapping[str, str] = field(default_factory=dict, repr=False)
    resolved_headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    timeout_seconds: float = 30.0
    max_reconnect_attempts: int = 1
    safe_tools: frozenset[str] = field(default_factory=frozenset)
    install_command: str | None = None
    documentation_url: str | None = None
    secrets: Sequence[str] = field(default_factory=tuple, repr=False)


@dataclass(frozen=True)
class SessionStoreConfig:
    """Configuration for append-only session persistence."""

    enabled: bool = True
    dir: Path = Path(".miniclaudecode/sessions")
    inactivity_notice_hours: int = 6


@dataclass(frozen=True)
class MemoryConfig:
    """Configuration for automatic note updates."""

    enabled: bool = True
    user_dir: Path = Path("~/.miniclaudecode/memory")
    project_dir: Path = Path(".miniclaudecode/memory")
    auto_every_turns: int = 10
    recent_messages: int = 20


@dataclass(frozen=True)
class SubAgentConfig:
    """Configuration for SubAgent definitions and task runtime."""

    enabled: bool = True
    project_dir: Path = Path(".miniclaudecode/agents")
    user_dir: Path = Path("~/.miniclaudecode/agents")
    include_verify_agent: bool = False
    foreground_timeout_seconds: float = 30.0
    background_timeout_seconds: float = 600.0
    background_allowed_tools: Sequence[str] = ("read_file", "glob_files", "grep_content")


@dataclass(frozen=True)
class AppConfig:
    """Application configuration with the active provider resolved."""

    active_provider: ProviderConfig
    providers: Mapping[str, ProviderConfig]
    mcp_servers: Sequence[McpServerConfig] = field(default_factory=tuple)
    context_management: ContextManagementConfig = field(default_factory=ContextManagementConfig)
    instructions: InstructionConfig = field(default_factory=InstructionConfig)
    sessions: SessionStoreConfig = field(default_factory=SessionStoreConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    subagents: SubAgentConfig = field(default_factory=SubAgentConfig)
    worktrees: WorktreeConfig = field(default_factory=WorktreeConfig)
    teams: AgentTeamConfig = field(default_factory=AgentTeamConfig)


def load_config(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    env_path: str | Path = DEFAULT_ENV_PATH,
) -> AppConfig:
    """Load app configuration from YAML and credentials from environment."""

    env_file = Path(env_path)
    if env_file.exists():
        load_dotenv(env_file, override=False)

    config_file = Path(config_path)
    if not config_file.exists():
        raise ConfigError(
            f"Configuration file '{config_file}' was not found. "
            "Copy config.example.yaml to config.yaml and adjust it for your environment."
        )

    raw_config = load_yaml_file(config_file)
    return parse_app_config(raw_config, environ=os.environ)


def load_yaml_file(path: str | Path) -> Mapping[str, Any]:
    """Read a YAML file and require a mapping at the root."""

    yaml_path = Path(path)
    if not yaml_path.exists():
        raise ConfigError(f"Configuration file '{yaml_path}' was not found.")

    try:
        with yaml_path.open("r", encoding="utf-8") as file:
            raw = yaml.safe_load(file)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Configuration file '{yaml_path}' is not valid YAML.") from exc
    except OSError as exc:
        raise ConfigError(f"Configuration file '{yaml_path}' could not be read.") from exc

    if raw is None:
        raise ConfigError(f"Configuration file '{yaml_path}' is empty.")
    if not isinstance(raw, Mapping):
        raise ConfigError(f"Configuration file '{yaml_path}' must contain a YAML object.")

    return raw


def parse_app_config(
    raw: Mapping[str, Any],
    *,
    environ: Mapping[str, str] | None = None,
) -> AppConfig:
    """Parse app configuration from an already-loaded mapping."""

    current_environ = os.environ if environ is None else environ

    active_provider_name = _require_non_empty_str(
        raw.get("active_provider"), "active_provider"
    )
    raw_providers = _require_mapping(raw.get("providers"), "providers")

    if active_provider_name not in raw_providers:
        raise ConfigError(
            f"Active provider '{active_provider_name}' is not defined in providers."
        )

    providers: dict[str, ProviderConfig] = {}
    for name, value in raw_providers.items():
        provider_name = _require_non_empty_str(name, "providers.<name>")
        provider_raw = _require_mapping(value, f"providers.{provider_name}")
        providers[provider_name] = parse_provider_config(
            provider_name,
            provider_raw,
            environ=current_environ,
            require_credential=provider_name == active_provider_name,
        )

    mcp_servers = parse_mcp_servers(_mcp_servers_value(raw), environ=current_environ)
    context_management = parse_context_management(raw.get("context_management"))
    instructions = parse_instructions(raw.get("instructions"))
    sessions = parse_sessions(raw.get("sessions"))
    memory = parse_memory(raw.get("memory"))
    subagents = parse_subagents(raw.get("subagents"))
    worktrees = parse_worktrees(raw.get("worktrees"))
    teams = parse_teams(raw.get("teams"))

    return AppConfig(
        active_provider=providers[active_provider_name],
        providers=providers,
        mcp_servers=mcp_servers,
        context_management=context_management,
        instructions=instructions,
        sessions=sessions,
        memory=memory,
        subagents=subagents,
        worktrees=worktrees,
        teams=teams,
    )


def parse_provider_config(
    name: str,
    raw: Mapping[str, Any],
    *,
    environ: Mapping[str, str],
    require_credential: bool = True,
) -> ProviderConfig:
    """Parse one provider configuration."""

    protocol = _require_non_empty_str(raw.get("protocol"), f"providers.{name}.protocol")
    if protocol not in SUPPORTED_PROTOCOLS:
        supported = ", ".join(sorted(SUPPORTED_PROTOCOLS))
        raise ConfigError(
            f"Provider '{name}' uses unsupported protocol '{protocol}'. "
            f"Supported protocols: {supported}."
        )

    model = _require_non_empty_str(raw.get("model"), f"providers.{name}.model")
    credential_env = _require_non_empty_str(
        raw.get("credential_env"), f"providers.{name}.credential_env"
    )
    credential = _resolve_credential(
        credential_env,
        environ=environ,
        required=require_credential,
    )

    base_url = _parse_optional_str(raw.get("base_url"), f"providers.{name}.base_url")
    if protocol == "openai-compatible" and base_url is None:
        raise ConfigError(f"Provider '{name}' with protocol 'openai-compatible' requires base_url.")

    thinking = ThinkingConfig()
    if protocol == "anthropic":
        thinking = _parse_thinking(raw.get("thinking"), f"providers.{name}.thinking")

    return ProviderConfig(
        name=name,
        protocol=protocol,
        model=model,
        credential_env=credential_env,
        credential=credential,
        base_url=base_url,
        thinking=thinking,
    )


def parse_mcp_servers(
    value: Any,
    *,
    environ: Mapping[str, str],
) -> tuple[McpServerConfig, ...]:
    """Parse optional MCP server configurations."""

    if value is None:
        return ()
    if not isinstance(value, list):
        raise ConfigError("mcp_servers must be a YAML list.")

    servers: list[McpServerConfig] = []
    seen_names: set[str] = set()
    for index, item in enumerate(value):
        field_name = f"mcp_servers[{index}]"
        raw = _require_mapping(item, field_name)
        server = parse_mcp_server_config(raw, field_name, environ=environ)
        if server.name in seen_names:
            raise ConfigError(f"mcp_servers contains duplicate server name '{server.name}'.")
        seen_names.add(server.name)
        servers.append(server)
    return tuple(servers)


def parse_mcp_server_config(
    raw: Mapping[str, Any],
    field_name: str,
    *,
    environ: Mapping[str, str],
) -> McpServerConfig:
    """Parse one MCP server configuration."""

    name = _require_non_empty_str(raw.get("name"), f"{field_name}.name")
    if SERVER_NAME_PATTERN.fullmatch(name) is None:
        raise ConfigError(
            f"{field_name}.name must start with a letter or underscore and contain only letters, digits, and underscores."
        )

    transport = _require_non_empty_str(raw.get("transport"), f"{field_name}.transport")
    if transport not in SUPPORTED_MCP_TRANSPORTS:
        supported = ", ".join(sorted(SUPPORTED_MCP_TRANSPORTS))
        raise ConfigError(f"{field_name}.transport must be one of: {supported}.")

    command = _parse_optional_str(raw.get("command"), f"{field_name}.command")
    url = _parse_optional_str(raw.get("url"), f"{field_name}.url")
    if transport == "stdio" and command is None:
        raise ConfigError(f"{field_name}.command is required when transport is stdio.")
    if transport == "streamable_http" and url is None:
        raise ConfigError(f"{field_name}.url is required when transport is streamable_http.")

    args = _parse_str_list(raw.get("args"), f"{field_name}.args")
    env = _parse_str_mapping(raw.get("env"), f"{field_name}.env")
    env_from = _parse_str_mapping(raw.get("env_from"), f"{field_name}.env_from")
    headers = _parse_str_mapping(raw.get("headers"), f"{field_name}.headers")
    headers_env = _parse_str_mapping(raw.get("headers_env"), f"{field_name}.headers_env")
    timeout_seconds = _parse_positive_float(
        raw.get("timeout_seconds", 30.0), f"{field_name}.timeout_seconds"
    )
    max_reconnect_attempts = _parse_non_negative_int(
        raw.get("max_reconnect_attempts", 1), f"{field_name}.max_reconnect_attempts"
    )
    safe_tools = frozenset(_parse_str_list(raw.get("safe_tools"), f"{field_name}.safe_tools"))
    install_command = _parse_optional_str(raw.get("install_command"), f"{field_name}.install_command")
    documentation_url = _parse_optional_str(raw.get("documentation_url"), f"{field_name}.documentation_url")

    resolved_env = _resolve_named_environment(env_from, environ=environ, field_name=f"{field_name}.env_from")
    resolved_headers = _resolve_named_environment(
        headers_env,
        environ=environ,
        field_name=f"{field_name}.headers_env",
    )
    secrets = tuple(value for value in (*resolved_env.values(), *resolved_headers.values()) if value)

    return McpServerConfig(
        name=name,
        transport=transport,
        command=command,
        args=args,
        url=url,
        env=env,
        env_from=env_from,
        headers=headers,
        headers_env=headers_env,
        resolved_env=resolved_env,
        resolved_headers=resolved_headers,
        timeout_seconds=timeout_seconds,
        max_reconnect_attempts=max_reconnect_attempts,
        safe_tools=safe_tools,
        install_command=install_command,
        documentation_url=documentation_url,
        secrets=secrets,
    )


def parse_context_management(value: Any) -> ContextManagementConfig:
    """Parse optional context-management configuration."""

    if value is None:
        return ContextManagementConfig()
    raw = _require_mapping(value, "context_management")
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ConfigError("context_management.enabled must be true or false.")

    artifact_dir_value = _parse_optional_str(raw.get("artifact_dir"), "context_management.artifact_dir")
    artifact_dir = Path(artifact_dir_value) if artifact_dir_value is not None else ContextManagementConfig().artifact_dir
    single_tool_result_token_limit = _parse_positive_int(
        raw.get("single_tool_result_token_limit", ContextManagementConfig().single_tool_result_token_limit),
        "context_management.single_tool_result_token_limit",
    )
    tool_result_batch_token_limit = _parse_positive_int(
        raw.get("tool_result_batch_token_limit", ContextManagementConfig().tool_result_batch_token_limit),
        "context_management.tool_result_batch_token_limit",
    )
    tool_result_preview_chars = _parse_positive_int(
        raw.get("tool_result_preview_chars", ContextManagementConfig().tool_result_preview_chars),
        "context_management.tool_result_preview_chars",
    )
    compact_trigger_ratio = _parse_ratio(
        raw.get("compact_trigger_ratio", ContextManagementConfig().compact_trigger_ratio),
        "context_management.compact_trigger_ratio",
    )
    compact_target_ratio = _parse_ratio(
        raw.get("compact_target_ratio", ContextManagementConfig().compact_target_ratio),
        "context_management.compact_target_ratio",
    )
    if compact_target_ratio >= compact_trigger_ratio:
        raise ConfigError("context_management.compact_target_ratio must be less than compact_trigger_ratio.")
    context_window_tokens = None
    if raw.get("context_window_tokens") is not None:
        context_window_tokens = _parse_positive_int(
            raw.get("context_window_tokens"),
            "context_management.context_window_tokens",
        )
    fallback_context_window_tokens = _parse_positive_int(
        raw.get("fallback_context_window_tokens", ContextManagementConfig().fallback_context_window_tokens),
        "context_management.fallback_context_window_tokens",
    )
    recent_message_groups_to_keep = _parse_positive_int(
        raw.get("recent_message_groups_to_keep", ContextManagementConfig().recent_message_groups_to_keep),
        "context_management.recent_message_groups_to_keep",
    )
    max_auto_compact_failures = _parse_positive_int(
        raw.get("max_auto_compact_failures", ContextManagementConfig().max_auto_compact_failures),
        "context_management.max_auto_compact_failures",
    )

    return ContextManagementConfig(
        enabled=enabled,
        artifact_dir=artifact_dir,
        single_tool_result_token_limit=single_tool_result_token_limit,
        tool_result_batch_token_limit=tool_result_batch_token_limit,
        tool_result_preview_chars=tool_result_preview_chars,
        compact_trigger_ratio=compact_trigger_ratio,
        compact_target_ratio=compact_target_ratio,
        context_window_tokens=context_window_tokens,
        fallback_context_window_tokens=fallback_context_window_tokens,
        recent_message_groups_to_keep=recent_message_groups_to_keep,
        max_auto_compact_failures=max_auto_compact_failures,
    )


def parse_instructions(value: Any) -> InstructionConfig:
    """Parse optional instruction loading configuration."""

    if value is None:
        return InstructionConfig()
    raw = _require_mapping(value, "instructions")
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ConfigError("instructions.enabled must be true or false.")
    project_file_value = _parse_optional_str(raw.get("project_file"), "instructions.project_file")
    user_file_value = _parse_optional_str(raw.get("user_file"), "instructions.user_file")
    max_include_depth = _parse_positive_int(
        raw.get("max_include_depth", InstructionConfig().max_include_depth),
        "instructions.max_include_depth",
    )
    return InstructionConfig(
        enabled=enabled,
        project_file=Path(project_file_value) if project_file_value is not None else InstructionConfig().project_file,
        user_file=Path(user_file_value) if user_file_value is not None else InstructionConfig().user_file,
        max_include_depth=max_include_depth,
    )


def parse_sessions(value: Any) -> SessionStoreConfig:
    """Parse optional session persistence configuration."""

    if value is None:
        return SessionStoreConfig()
    raw = _require_mapping(value, "sessions")
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ConfigError("sessions.enabled must be true or false.")
    dir_value = _parse_optional_str(raw.get("dir"), "sessions.dir")
    inactivity_notice_hours = _parse_positive_int(
        raw.get("inactivity_notice_hours", SessionStoreConfig().inactivity_notice_hours),
        "sessions.inactivity_notice_hours",
    )
    return SessionStoreConfig(
        enabled=enabled,
        dir=Path(dir_value) if dir_value is not None else SessionStoreConfig().dir,
        inactivity_notice_hours=inactivity_notice_hours,
    )


def parse_memory(value: Any) -> MemoryConfig:
    """Parse optional automatic memory configuration."""

    if value is None:
        return MemoryConfig()
    raw = _require_mapping(value, "memory")
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ConfigError("memory.enabled must be true or false.")
    user_dir_value = _parse_optional_str(raw.get("user_dir"), "memory.user_dir")
    project_dir_value = _parse_optional_str(raw.get("project_dir"), "memory.project_dir")
    auto_every_turns = _parse_positive_int(
        raw.get("auto_every_turns", MemoryConfig().auto_every_turns),
        "memory.auto_every_turns",
    )
    recent_messages = _parse_positive_int(
        raw.get("recent_messages", MemoryConfig().recent_messages),
        "memory.recent_messages",
    )
    return MemoryConfig(
        enabled=enabled,
        user_dir=Path(user_dir_value) if user_dir_value is not None else MemoryConfig().user_dir,
        project_dir=Path(project_dir_value) if project_dir_value is not None else MemoryConfig().project_dir,
        auto_every_turns=auto_every_turns,
        recent_messages=recent_messages,
    )


def parse_subagents(value: Any) -> SubAgentConfig:
    """Parse optional SubAgent configuration."""

    if value is None:
        return SubAgentConfig()
    raw = _require_mapping(value, "subagents")
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ConfigError("subagents.enabled must be true or false.")
    project_dir_value = _parse_optional_str(raw.get("project_dir"), "subagents.project_dir")
    user_dir_value = _parse_optional_str(raw.get("user_dir"), "subagents.user_dir")
    include_verify_agent = raw.get("include_verify_agent", False)
    if not isinstance(include_verify_agent, bool):
        raise ConfigError("subagents.include_verify_agent must be true or false.")
    foreground_timeout_seconds = _parse_positive_float(
        raw.get("foreground_timeout_seconds", SubAgentConfig().foreground_timeout_seconds),
        "subagents.foreground_timeout_seconds",
    )
    background_timeout_seconds = _parse_positive_float(
        raw.get("background_timeout_seconds", SubAgentConfig().background_timeout_seconds),
        "subagents.background_timeout_seconds",
    )
    background_allowed_tools = _parse_str_list(
        raw.get("background_allowed_tools", list(SubAgentConfig().background_allowed_tools)),
        "subagents.background_allowed_tools",
    )
    return SubAgentConfig(
        enabled=enabled,
        project_dir=Path(project_dir_value) if project_dir_value is not None else SubAgentConfig().project_dir,
        user_dir=Path(user_dir_value) if user_dir_value is not None else SubAgentConfig().user_dir,
        include_verify_agent=include_verify_agent,
        foreground_timeout_seconds=foreground_timeout_seconds,
        background_timeout_seconds=background_timeout_seconds,
        background_allowed_tools=background_allowed_tools,
    )


def parse_teams(value: Any) -> AgentTeamConfig:
    """Parse optional AgentTeam configuration."""

    if value is None:
        return AgentTeamConfig()
    raw = _require_mapping(value, "teams")
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ConfigError("teams.enabled must be true or false.")
    root_dir_value = _parse_optional_str(raw.get("root_dir"), "teams.root_dir")
    backend_priority = _parse_str_list(
        raw.get("backend_priority", list(AgentTeamConfig().backend_priority)),
        "teams.backend_priority",
    )
    if not backend_priority:
        raise ConfigError("teams.backend_priority must not be empty.")
    valid_backends = {item.value for item in TeamBackendKind}
    for backend in backend_priority:
        if backend not in valid_backends:
            supported = ", ".join(sorted(valid_backends))
            raise ConfigError(f"teams.backend_priority contains unsupported backend '{backend}'. Supported: {supported}.")
    allow_backend_fallback = raw.get("allow_backend_fallback", False)
    if not isinstance(allow_backend_fallback, bool):
        raise ConfigError("teams.allow_backend_fallback must be true or false.")
    mailbox_dir_value = _parse_optional_str(raw.get("mailbox_dir"), "teams.mailbox_dir")
    transcript_dir_value = _parse_optional_str(raw.get("transcript_dir"), "teams.transcript_dir")
    tasks_file_value = _parse_optional_str(raw.get("tasks_file"), "teams.tasks_file")
    registry_file_value = _parse_optional_str(raw.get("registry_file"), "teams.registry_file")
    default_permission_mode = _parse_optional_str(raw.get("default_permission_mode"), "teams.default_permission_mode") or AgentTeamConfig().default_permission_mode
    coordinator_enabled = raw.get("coordinator_enabled", False)
    if not isinstance(coordinator_enabled, bool):
        raise ConfigError("teams.coordinator_enabled must be true or false.")
    return AgentTeamConfig(
        enabled=enabled,
        root_dir=Path(root_dir_value) if root_dir_value is not None else AgentTeamConfig().root_dir,
        backend_priority=backend_priority,
        allow_backend_fallback=allow_backend_fallback,
        mailbox_dir=Path(mailbox_dir_value) if mailbox_dir_value is not None else AgentTeamConfig().mailbox_dir,
        transcript_dir=Path(transcript_dir_value) if transcript_dir_value is not None else AgentTeamConfig().transcript_dir,
        tasks_file=Path(tasks_file_value) if tasks_file_value is not None else AgentTeamConfig().tasks_file,
        registry_file=Path(registry_file_value) if registry_file_value is not None else AgentTeamConfig().registry_file,
        default_permission_mode=default_permission_mode,
        coordinator_enabled=coordinator_enabled,
    )



def parse_worktrees(value: Any) -> WorktreeConfig:
    """Parse optional Git worktree isolation configuration."""

    if value is None:
        return WorktreeConfig()
    raw = _require_mapping(value, "worktrees")
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ConfigError("worktrees.enabled must be true or false.")
    root_dir_value = _parse_optional_str(raw.get("root_dir"), "worktrees.root_dir")
    state_file_value = _parse_optional_str(raw.get("state_file"), "worktrees.state_file")
    branch_prefix = _parse_optional_str(raw.get("branch_prefix"), "worktrees.branch_prefix") or WorktreeConfig().branch_prefix
    max_name_length = _parse_positive_int(raw.get("max_name_length", WorktreeConfig().max_name_length), "worktrees.max_name_length")
    max_segment_length = _parse_positive_int(
        raw.get("max_segment_length", WorktreeConfig().max_segment_length),
        "worktrees.max_segment_length",
    )
    copy_files = _parse_str_list(raw.get("copy_files", list(WorktreeConfig().copy_files)), "worktrees.copy_files")
    link_dirs = _parse_str_list(raw.get("link_dirs", list(WorktreeConfig().link_dirs)), "worktrees.link_dirs")
    ignored_file_rules = _parse_ignored_file_rules(raw.get("ignored_file_rules"))
    temporary_ttl_seconds = _parse_positive_float(
        raw.get("temporary_ttl_seconds", WorktreeConfig().temporary_ttl_seconds),
        "worktrees.temporary_ttl_seconds",
    )
    cleanup_interval_seconds = _parse_positive_float(
        raw.get("cleanup_interval_seconds", WorktreeConfig().cleanup_interval_seconds),
        "worktrees.cleanup_interval_seconds",
    )
    confirm_required_for_dirty_delete = raw.get("confirm_required_for_dirty_delete", True)
    if not isinstance(confirm_required_for_dirty_delete, bool):
        raise ConfigError("worktrees.confirm_required_for_dirty_delete must be true or false.")
    return WorktreeConfig(
        enabled=enabled,
        root_dir=Path(root_dir_value) if root_dir_value is not None else WorktreeConfig().root_dir,
        state_file=Path(state_file_value) if state_file_value is not None else WorktreeConfig().state_file,
        branch_prefix=branch_prefix,
        max_name_length=max_name_length,
        max_segment_length=max_segment_length,
        copy_files=copy_files,
        link_dirs=link_dirs,
        ignored_file_rules=ignored_file_rules,
        temporary_ttl_seconds=temporary_ttl_seconds,
        cleanup_interval_seconds=cleanup_interval_seconds,
        confirm_required_for_dirty_delete=confirm_required_for_dirty_delete,
    )


def _parse_ignored_file_rules(value: Any) -> tuple[IgnoredFileRule, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ConfigError("worktrees.ignored_file_rules must be a list.")
    rules: list[IgnoredFileRule] = []
    for index, item in enumerate(value):
        field = f"worktrees.ignored_file_rules[{index}]"
        if isinstance(item, str):
            rules.append(IgnoredFileRule(path=_require_non_empty_str(item, field)))
            continue
        raw = _require_mapping(item, field)
        path = _require_non_empty_str(raw.get("path"), f"{field}.path")
        required = raw.get("required", False)
        if not isinstance(required, bool):
            raise ConfigError(f"{field}.required must be true or false.")
        rules.append(IgnoredFileRule(path=path, required=required))
    return tuple(rules)


def _mcp_servers_value(raw: Mapping[str, Any]) -> Any:
    has_mcp = "mcp_servers" in raw
    has_external = "external_servers" in raw
    if has_mcp and has_external:
        raise ConfigError("Use mcp_servers instead of external_servers; both fields cannot be present.")
    if has_mcp:
        return raw.get("mcp_servers")
    return raw.get("external_servers")


def _resolve_credential(
    credential_env: str,
    *,
    environ: Mapping[str, str],
    required: bool,
) -> str | None:
    value = environ.get(credential_env)
    if value is None or value.strip() == "":
        if required:
            raise ConfigError(f"Required environment variable '{credential_env}' is missing or empty.")
        return None
    return value


def _resolve_named_environment(
    names: Mapping[str, str],
    *,
    environ: Mapping[str, str],
    field_name: str,
) -> dict[str, str]:
    resolved: dict[str, str] = {}
    for target_name, source_name in names.items():
        value = environ.get(source_name)
        if value is None or value.strip() == "":
            raise ConfigError(f"Required environment variable '{source_name}' for {field_name}.{target_name} is missing or empty.")
        resolved[target_name] = value
    return resolved


def _parse_thinking(value: Any, field_name: str) -> ThinkingConfig:
    if value is None:
        return ThinkingConfig()

    raw = _require_mapping(value, field_name)

    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ConfigError(f"{field_name}.enabled must be true or false.")

    effort = _parse_optional_str(raw.get("effort"), f"{field_name}.effort") or "high"
    if effort not in SUPPORTED_THINKING_EFFORTS:
        supported = ", ".join(sorted(SUPPORTED_THINKING_EFFORTS))
        raise ConfigError(f"{field_name}.effort must be one of: {supported}.")

    display = _parse_optional_str(raw.get("display"), f"{field_name}.display") or "omitted"
    if display not in SUPPORTED_THINKING_DISPLAYS:
        supported = ", ".join(sorted(SUPPORTED_THINKING_DISPLAYS))
        raise ConfigError(f"{field_name}.display must be one of: {supported}.")

    return ThinkingConfig(enabled=enabled, effort=effort, display=display)


def _require_mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigError(f"{field_name} must be a YAML object.")
    return value


def _require_non_empty_str(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or value.strip() == "":
        raise ConfigError(f"{field_name} must be a non-empty string.")
    return value.strip()


def _parse_optional_str(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or value.strip() == "":
        raise ConfigError(f"{field_name} must be a non-empty string when provided.")
    return value.strip()


def _parse_str_mapping(value: Any, field_name: str) -> dict[str, str]:
    if value is None:
        return {}
    raw = _require_mapping(value, field_name)
    parsed: dict[str, str] = {}
    for key, item in raw.items():
        parsed_key = _require_non_empty_str(key, f"{field_name}.<key>")
        parsed[parsed_key] = _require_non_empty_str(item, f"{field_name}.{parsed_key}")
    return parsed


def _parse_str_list(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ConfigError(f"{field_name} must be a list of strings.")
    return tuple(_require_non_empty_str(item, f"{field_name}[]") for item in value)


def _parse_positive_float(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{field_name} must be a positive number.")
    parsed = float(value)
    if parsed <= 0:
        raise ConfigError(f"{field_name} must be a positive number.")
    return parsed


def _parse_non_negative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{field_name} must be a non-negative integer.")
    if value < 0:
        raise ConfigError(f"{field_name} must be a non-negative integer.")
    return value


def _parse_positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{field_name} must be a positive integer.")
    if value <= 0:
        raise ConfigError(f"{field_name} must be a positive integer.")
    return value


def _parse_ratio(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{field_name} must be a number between 0 and 1.")
    parsed = float(value)
    if parsed <= 0 or parsed >= 1:
        raise ConfigError(f"{field_name} must be a number between 0 and 1.")
    return parsed


# Backward-compatible names for older internal imports and tests.
ExternalServerConfig = McpServerConfig
parse_external_servers = parse_mcp_servers
parse_external_server_config = parse_mcp_server_config
SUPPORTED_EXTERNAL_TRANSPORTS = SUPPORTED_MCP_TRANSPORTS
