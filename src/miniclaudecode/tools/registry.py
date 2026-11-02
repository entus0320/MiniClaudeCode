"""Tool registry for MiniClaudeCode."""

from __future__ import annotations

from miniclaudecode.tools.base import Tool


class ToolRegistryError(Exception):
    """Raised when tool registration or lookup fails."""


class ToolRegistry:
    """Keep tools registered by unique name in stable order."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Register a tool by name."""

        if tool.name in self._tools:
            raise ToolRegistryError(f"Tool '{tool.name}' is already registered.")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        """Return a registered tool by name."""

        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolRegistryError(f"Unknown tool '{name}'.") from exc

    def list_tools(self) -> list[Tool]:
        """Return registered tools in registration order."""

        return list(self._tools.values())

    def list_tool_names(self) -> list[str]:
        """Return registered tool names in registration order."""

        return list(self._tools)

    def copy(self) -> "ToolRegistry":
        """Return a new registry with the same tool instances in stable order."""

        registry = ToolRegistry()
        for tool in self.list_tools():
            registry.register(tool)
        return registry

    def filtered(self, names: list[str] | tuple[str, ...]) -> "ToolRegistry":
        """Return a new registry containing the given names in current registry order."""

        allowed = set(names)
        registry = ToolRegistry()
        for name, tool in self._tools.items():
            if name in allowed:
                registry.register(tool)
        return registry

    def without(self, names: list[str] | tuple[str, ...]) -> "ToolRegistry":
        """Return a new registry excluding the given tool names."""

        blocked = set(names)
        registry = ToolRegistry()
        for name, tool in self._tools.items():
            if name not in blocked:
                registry.register(tool)
        return registry

    def unregister(self, name: str) -> None:
        """Remove a registered tool by name."""

        try:
            del self._tools[name]
        except KeyError as exc:
            raise ToolRegistryError(f"Unknown tool '{name}'.") from exc

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)
