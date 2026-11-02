"""Connection manager for MCP tool servers."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from miniclaudecode.config import McpServerConfig
from miniclaudecode.mcp.client import McpCallResult, McpServerConnection, McpServerError
from miniclaudecode.mcp.tools import McpTool
from miniclaudecode.tools.registry import ToolRegistry, ToolRegistryError


class McpConnectionManager:
    """Manage MCP server connections and tool registration."""

    def __init__(self, configs: Sequence[McpServerConfig]) -> None:
        self.configs = tuple(configs)
        self.connections: dict[str, McpServerConnection] = {}
        self.startup_warnings: list[str] = []

    def discover_tools(self, registry: ToolRegistry) -> None:
        """Connect configured MCP servers and register discovered tools."""

        for config in self.configs:
            connection = McpServerConnection(config)
            try:
                connection.connect()
            except McpServerError as exc:
                self.startup_warnings.append(_startup_failure_message(config, str(exc)))
                connection.close()
                continue
            self.connections[config.name] = connection
            for description in connection.tools:
                try:
                    registry.register(McpTool(description, self))
                except (ValueError, ToolRegistryError) as exc:
                    self.startup_warnings.append(
                        f"MCP server '{config.name}' 的工具 '{description.name}' 无法注册：{exc}"
                    )

    def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> McpCallResult:
        """Call one MCP tool through a cached connection."""

        connection = self.connections.get(server_name)
        if connection is None:
            return McpCallResult(success=False, error=f"MCP server '{server_name}' 未连接。")
        return connection.call_tool(tool_name, arguments)

    def close_all(self) -> None:
        """Close all active MCP server connections."""

        for connection in self.connections.values():
            connection.close()
        self.connections.clear()


def _startup_failure_message(config: McpServerConfig, reason: str) -> str:
    parts = [f"MCP server '{config.name}' 初始化失败：{reason} 该 server 的工具未注册。"]
    if config.install_command is not None:
        parts.append(f"可手动运行安装命令：{config.install_command}")
    if config.documentation_url is not None:
        parts.append(f"参考文档：{config.documentation_url}")
    return " ".join(parts)

