"""Core command abstractions for MiniClaudeCode."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class CommandType(Enum):
    """Execution category for a slash command."""

    LOCAL = "local"
    UI_STATE = "ui_state"
    PROMPT = "prompt"


@dataclass(frozen=True)
class CommandInvocation:
    """A parsed slash command input."""

    raw: str
    name: str
    args: str = ""


@dataclass(frozen=True)
class CommandResult:
    """Result returned by a command handler."""

    handled: bool = True
    message: str | None = None


class CommandRegistrationError(ValueError):
    """Raised when a command name or alias cannot be registered."""


class CommandUI(Protocol):
    """UI operations available to command handlers."""

    def append_system_message(self, content: str) -> None: ...

    def exit_app(self, code: int = 0) -> None: ...

    def interrupt(self) -> None: ...

    def is_generating(self) -> bool: ...

    def has_pending_confirmation(self) -> bool: ...

    def resolve_confirmation_command(self, name: str) -> bool: ...


@dataclass(frozen=True)
class CommandContext:
    """Runtime context passed to command handlers."""

    ui: CommandUI
    registry: CommandRegistry
    app: object | None = None


CommandHandler = Callable[[CommandContext, CommandInvocation], CommandResult | None]


@dataclass(frozen=True)
class Command:
    """Registered command metadata and handler."""

    name: str
    aliases: tuple[str, ...] = ()
    description: str = ""
    usage: str = ""
    type: CommandType = CommandType.LOCAL
    argument_hint: str = ""
    handler: CommandHandler | None = None
    hidden: bool = False
    allowed_while_generating: bool = False
    allowed_during_confirmation: bool = False


@dataclass(frozen=True)
class CommandCompletionItem:
    """A visible command or alias completion candidate."""

    value: str
    command_name: str
    description: str
    argument_hint: str = ""
    is_alias: bool = False


class CommandParser:
    """Parse user input into slash command invocations."""

    def parse(self, text: str) -> CommandInvocation | None:
        value = text.strip()
        if not value.startswith("/"):
            return None
        body = value[1:]
        if body == "":
            return CommandInvocation(raw=value, name="", args="")
        parts = body.split(maxsplit=1)
        name = parts[0].lower()
        args = parts[1].strip() if len(parts) == 2 else ""
        return CommandInvocation(raw=value, name=name, args=args)

    def completion_prefix(self, text: str) -> str | None:
        value = text.strip()
        if not value.startswith("/"):
            return None
        body = value[1:]
        if body == "":
            return ""
        if body[:1].isspace() or any(char.isspace() for char in body):
            return None
        return body.lower()


class CommandRegistry:
    """Central registry for slash commands."""

    def __init__(self) -> None:
        self._commands: dict[str, Command] = {}
        self._lookup: dict[str, Command] = {}

    def register(self, command: Command) -> None:
        name = _canonical_token(command.name)
        if not name:
            raise CommandRegistrationError("命令名不能为空。")
        if name in self._lookup:
            raise CommandRegistrationError(f"命令名或别名冲突：{command.name}")

        seen = {name}
        aliases: list[str] = []
        for alias in command.aliases:
            canonical = _canonical_token(alias)
            if not canonical:
                raise CommandRegistrationError("命令别名不能为空。")
            if canonical in seen:
                raise CommandRegistrationError(f"命令内部名称或别名重复：{alias}")
            if canonical in self._lookup:
                raise CommandRegistrationError(f"命令名或别名冲突：{alias}")
            seen.add(canonical)
            aliases.append(canonical)

        normalized = Command(
            name=name,
            aliases=tuple(aliases),
            description=command.description,
            usage=command.usage or f"/{name}",
            type=command.type,
            argument_hint=command.argument_hint,
            handler=command.handler,
            hidden=command.hidden,
            allowed_while_generating=command.allowed_while_generating,
            allowed_during_confirmation=command.allowed_during_confirmation,
        )
        self._commands[name] = normalized
        self._lookup[name] = normalized
        for alias in aliases:
            self._lookup[alias] = normalized

    def get(self, name_or_alias: str) -> Command | None:
        return self._lookup.get(_canonical_token(name_or_alias))

    def visible_commands(self) -> list[Command]:
        return [command for command in self._commands.values() if not command.hidden]

    def all_commands(self) -> list[Command]:
        return list(self._commands.values())

    def unregister(self, name: str) -> None:
        """Remove a command and its aliases by registered command name."""

        command = self._commands.pop(_canonical_token(name), None)
        if command is None:
            return
        self._lookup.pop(command.name, None)
        for alias in command.aliases:
            self._lookup.pop(alias, None)

    def completions(self, prefix: str) -> list[CommandCompletionItem]:
        canonical_prefix = _canonical_token(prefix)
        items: list[CommandCompletionItem] = []
        for command in self.visible_commands():
            tokens = [(command.name, False), *((alias, True) for alias in command.aliases)]
            for token, is_alias in tokens:
                if token.startswith(canonical_prefix):
                    items.append(
                        CommandCompletionItem(
                            value=f"/{token}",
                            command_name=command.name,
                            description=command.description,
                            argument_hint=command.argument_hint,
                            is_alias=is_alias,
                        )
                    )
        return sorted(items, key=lambda item: (item.value, item.command_name))

    def suggestions(self, name: str) -> list[str]:
        canonical = _canonical_token(name)
        if not canonical:
            return [f"/{command.name}" for command in self.visible_commands()[:5]]
        matches = [item.value for item in self.completions(canonical)]
        if matches:
            return matches[:5]
        prefix_matches = [f"/{command.name}" for command in self.visible_commands() if command.name.startswith(canonical[:1])]
        return prefix_matches[:5]


def _canonical_token(token: str) -> str:
    value = token.strip().lower()
    if value.startswith("/"):
        value = value[1:]
    if any(char.isspace() for char in value):
        raise CommandRegistrationError(f"命令名或别名不能包含空白字符：{token}")
    return value
