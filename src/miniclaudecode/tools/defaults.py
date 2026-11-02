"""Default MiniClaudeCode tool registry."""

from __future__ import annotations

from miniclaudecode.tools.edit_file import EditFileTool
from miniclaudecode.tools.glob_files import GlobFilesTool
from miniclaudecode.tools.grep_content import GrepContentTool
from miniclaudecode.tools.read_file import ReadFileTool
from miniclaudecode.tools.registry import ToolRegistry
from miniclaudecode.tools.run_command import RunCommandTool
from miniclaudecode.tools.write_file import WriteFileTool


def create_default_tool_registry() -> ToolRegistry:
    """Create the default six-tool registry."""

    registry = ToolRegistry()
    registry.register(ReadFileTool())
    registry.register(WriteFileTool())
    registry.register(EditFileTool())
    registry.register(RunCommandTool())
    registry.register(GlobFilesTool())
    registry.register(GrepContentTool())
    return registry
