"""Central command system for MiniClaudeCode."""

from miniclaudecode.commands.builtin import create_builtin_command_registry
from miniclaudecode.commands.core import (
    Command,
    CommandCompletionItem,
    CommandContext,
    CommandInvocation,
    CommandParser,
    CommandRegistrationError,
    CommandRegistry,
    CommandResult,
    CommandType,
)

__all__ = [
    "Command",
    "CommandCompletionItem",
    "CommandContext",
    "CommandInvocation",
    "CommandParser",
    "CommandRegistrationError",
    "CommandRegistry",
    "CommandResult",
    "CommandType",
    "create_builtin_command_registry",
]
