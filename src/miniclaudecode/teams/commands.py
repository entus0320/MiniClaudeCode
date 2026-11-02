"""Slash commands for AgentTeam management."""

from __future__ import annotations

from miniclaudecode.commands.core import Command, CommandInvocation, CommandRegistry, CommandResult, CommandType
from miniclaudecode.teams.core import TeamBackendKind, TeamError
from miniclaudecode.teams.lifecycle import TeamLifecycleManager


def register_team_commands(command_registry: CommandRegistry, manager: TeamLifecycleManager) -> None:
    """Register /team commands."""

    command_registry.register(
        Command(
            name="team",
            description="管理 AgentTeam 团队、队员、任务和邮箱。",
            usage="/team <create|list|status|members|spawn|dispatch|tasks|inbox|send|recover|delete> [args]",
            type=CommandType.LOCAL,
            argument_hint="<create|list|status|members|spawn|dispatch|tasks|inbox|send|recover|delete> [args]",
            handler=lambda ctx, invocation: _handle_team(manager, invocation),
            allowed_during_confirmation=False,
        )
    )


def _handle_team(manager: TeamLifecycleManager, invocation: CommandInvocation) -> CommandResult:
    parts = invocation.args.split()
    if not parts:
        return CommandResult(message="用法：/team <create|list|status|members|tasks|inbox|send|recover|delete> [args]")
    action = parts[0]
    try:
        if action == "create":
            if len(parts) < 2:
                return CommandResult(message="用法：/team create <name>")
            result = manager.create_team(parts[1])
            return CommandResult(message=result.message)
        if action == "list":
            teams = manager.list_teams()
            if not teams:
                return CommandResult(message="暂无 AgentTeam。")
            lines = ["AgentTeam："]
            for team in teams:
                archived = " archived" if team.archived else ""
                lines.append(f"- {team.name}{archived} | members={len(team.members)} | {team.root}")
            return CommandResult(message="\n".join(lines))
        if action in {"status", "members", "tasks", "inbox", "send", "recover", "delete", "add-member", "member", "spawn", "dispatch"} and len(parts) < 2:
            return CommandResult(message=f"用法：/team {action} <team> [args]")
        if action == "status":
            team = manager.get_team(parts[1])
            return CommandResult(message=f"AgentTeam {team.name}\n- archived：{team.archived}\n- members：{len(team.members)}\n- path：{team.root}")
        if action == "members":
            team = manager.get_team(parts[1])
            if not team.members:
                return CommandResult(message="暂无队员。")
            return CommandResult(message="队员：\n" + "\n".join(f"- {m.name} | {m.agent_id} | {m.status.value} | {m.backend.value}" for m in team.members))
        if action in {"add-member", "member", "spawn"}:
            if len(parts) < 3:
                return CommandResult(message=f"用法：/team {action} <team> <member> [backend]")
            backend = TeamBackendKind(parts[3]) if len(parts) > 3 else TeamBackendKind.IN_PROCESS
            result = manager.add_member(parts[1], parts[2], backend=backend)
            return CommandResult(message=result.message)
        if action == "tasks":
            return CommandResult(message=manager.list_tasks_text(parts[1]))
        if action == "dispatch":
            if len(parts) < 6:
                return CommandResult(message="用法：/team dispatch <team> <member> <append|write|read> <path> <content>")
            mode = parts[3]
            path = parts[4]
            content = " ".join(parts[5:]) if len(parts) > 5 else None
            result = manager.dispatch_member_task(parts[1], parts[2], task=f"dispatch {mode} {path}", path=path, content=content, mode=mode)
            return CommandResult(message=result.message)
        if action == "send":
            if len(parts) < 4:
                return CommandResult(message="用法：/team send <team> <member> <message>")
            message = " ".join(parts[3:])
            result = manager.send_message(parts[1], parts[2], summary=message[:80], body=message)
            return CommandResult(message=result.message)
        if action == "inbox":
            if len(parts) < 3:
                return CommandResult(message="用法：/team inbox <team> <member>")
            team = manager.get_team(parts[1])
            member = next((item for item in team.members if item.name == parts[2]), None)
            if member is None:
                return CommandResult(message=f"队员不存在：{parts[2]}")
            return CommandResult(message=f"队员 {member.name} 邮箱：{member.mailbox_path}")
        if action == "recover":
            team = manager.get_team(parts[1])
            return CommandResult(message=f"已从磁盘恢复 AgentTeam：{team.name}")
        if action == "delete":
            confirm = "--confirm" in parts[2:]
            result = manager.delete_team(parts[1], confirm=confirm)
            return CommandResult(message=result.message)
    except (TeamError, ValueError) as exc:
        return CommandResult(message=f"AgentTeam 命令失败：{exc}")
    return CommandResult(message="未知 team 子命令。用法：/team <create|list|status|members|tasks|inbox|send|recover|delete> [args]")
