"""AgentTeam lifecycle management."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from miniclaudecode.teams.core import AgentTeam, TeamBackendKind, TeamMemberStatus, TeamOperationResult
from miniclaudecode.teams.mailbox import MailboxStore
from miniclaudecode.teams.messages import make_text_message
from miniclaudecode.teams.registry import NameRegistry
from miniclaudecode.teams.roster import TeamRoster
from miniclaudecode.teams.store import AgentTeamStore
from miniclaudecode.teams.tasks import SharedTaskBoard
from miniclaudecode.teams.wakeup import WakeupDispatcher
from miniclaudecode.worktrees.manager import WorktreeManager


class TeamLifecycleManager:
    """Facade for AgentTeam creation, members, tasks and messaging."""

    def __init__(self, store: AgentTeamStore, *, wakeup: WakeupDispatcher | None = None, worktree_manager: WorktreeManager | None = None) -> None:
        self.store = store
        self.roster = TeamRoster()
        self.wakeup = wakeup
        self.worktree_manager = worktree_manager
        self._notifications: list[str] = []

    def create_team(self, name: str, *, lead_id: str | None = None) -> TeamOperationResult:
        team = self.store.create(name, lead_id=lead_id)
        team.mailbox_root.mkdir(parents=True, exist_ok=True)
        team.transcript_root.mkdir(parents=True, exist_ok=True)
        NameRegistry(team.registry_path).save_team(team)
        SharedTaskBoard(team.tasks_path).save([])
        return TeamOperationResult(True, f"已创建 AgentTeam：{team.name}", metadata={"team": team.name, "path": str(team.root)})

    def list_teams(self) -> tuple[AgentTeam, ...]:
        return self.store.list_teams()

    def get_team(self, name: str) -> AgentTeam:
        return self.store.load(name)

    def archive_team(self, name: str) -> TeamOperationResult:
        team = self.store.archive(name)
        return TeamOperationResult(True, f"已归档 AgentTeam：{team.name}", metadata={"team": team.name})

    def delete_team(self, name: str, *, confirm: bool = False) -> TeamOperationResult:
        if not confirm:
            return TeamOperationResult(False, "TeamDelete 涉及删除或归档团队状态，请使用 --confirm 显式确认；默认建议只归档。", requires_confirmation=True)
        return self.archive_team(name)

    def add_member(
        self,
        team_name: str,
        member_name: str,
        *,
        role: str = "member",
        backend: TeamBackendKind = TeamBackendKind.IN_PROCESS,
        workdir: Path | None = None,
    ) -> TeamOperationResult:
        team = self.store.load(team_name)
        member_workdir = workdir
        worktree_name = f"teams/{team.name}/{member_name}"
        if member_workdir is None and self.worktree_manager is not None:
            worktree_result = self.worktree_manager.create(worktree_name, temporary=False)
            if not worktree_result.success or worktree_result.record is None:
                return TeamOperationResult(False, f"创建队员 Worktree 失败：{worktree_result.message}")
            member_workdir = worktree_result.record.path
        updated, member = self.roster.add_member(team, member_name, role=role, backend=backend, workdir=member_workdir)
        if member_workdir is not None and member.workdir != member_workdir:
            member = replace(member, workdir=member_workdir)
            updated = self.roster.update_member(updated, member)
        if self.worktree_manager is not None:
            member = replace(member, metadata={**member.metadata, "worktree": worktree_name})
            updated = self.roster.update_member(updated, member)
        self.store.save(updated)
        NameRegistry(updated.registry_path).save_team(updated)
        member.mailbox_path.parent.mkdir(parents=True, exist_ok=True)
        member.transcript_path.parent.mkdir(parents=True, exist_ok=True)
        return TeamOperationResult(True, f"已添加队员：{member.name} ({member.agent_id})\n- 工作目录：{member.workdir}", metadata={"agent_id": member.agent_id, "workdir": str(member.workdir), "worktree": member.metadata.get("worktree")})

    def create_task(self, team_name: str, title: str, *, description: str = "", owner: str | None = None, dependencies: tuple[str, ...] = ()) -> TeamOperationResult:
        team = self.store.load(team_name)
        task = SharedTaskBoard(team.tasks_path).create(title, description=description, owner_member=owner, dependencies=dependencies)
        return TeamOperationResult(True, f"已创建共享任务：{task.task_id} {task.title}", metadata={"task_id": task.task_id})

    def list_tasks_text(self, team_name: str) -> str:
        team = self.store.load(team_name)
        tasks = SharedTaskBoard(team.tasks_path).list_tasks()
        if not tasks:
            return "暂无共享任务。"
        lines = ["共享任务："]
        for task in tasks:
            owner = task.owner_member or "未分派"
            deps = f" deps={','.join(task.dependencies)}" if task.dependencies else ""
            lines.append(f"- {task.task_id} | {task.status.value} | {owner} | {task.title}{deps}")
        return "\n".join(lines)

    def send_message(self, team_name: str, to_member: str, *, summary: str, body: str, from_member: str | None = None, broadcast: bool = False) -> TeamOperationResult:
        team = self.store.load(team_name)
        targets = [member for member in team.members if member.status is not TeamMemberStatus.ARCHIVED] if broadcast else [NameRegistry(team.registry_path).resolve_member(team, to_member)]
        if not targets:
            return TeamOperationResult(False, "没有可投递的队员。")
        mailbox = MailboxStore(team.mailbox_root)
        diagnostics = []
        delivered: list[str] = []
        for member in targets:
            message = make_text_message(
                team_id=team.team_id,
                to_agent_id=member.agent_id,
                to_member=member.name,
                from_member=from_member,
                summary=summary,
                body=body,
            )
            mailbox.append(message)
            delivered.append(member.name)
            if self.wakeup is not None:
                result = self.wakeup.wake(member, message)
                if not result.success:
                    diagnostics.extend(result.diagnostics)
        text = f"已投递消息：{', '.join(delivered)}"
        self._notifications.append(text)
        return TeamOperationResult(True, text, diagnostics=tuple(diagnostics), metadata={"members": delivered})

    def dispatch_member_task(
        self,
        team_name: str,
        member_name: str,
        *,
        task: str,
        path: str | None = None,
        content: str | None = None,
        mode: str = "append",
    ) -> TeamOperationResult:
        team = self.store.load(team_name)
        member = NameRegistry(team.registry_path).resolve_member(team, member_name)
        if path is None:
            message = make_text_message(
                team_id=team.team_id,
                to_agent_id=member.agent_id,
                to_member=member.name,
                summary=task[:80] or "新任务",
                body=task,
            )
            MailboxStore(team.mailbox_root).append(message)
            return TeamOperationResult(True, f"已向队员 {member.name} 派发任务。", metadata={"agent_id": member.agent_id, "workdir": str(member.workdir)})
        target = _resolve_member_file(member.workdir, path)
        if mode not in {"append", "write", "read"}:
            return TeamOperationResult(False, "TeamDispatch.mode 仅支持 append、write 或 read。")
        if mode == "read":
            if not target.exists():
                return TeamOperationResult(False, f"队员文件不存在：{path}", metadata={"workdir": str(member.workdir)})
            return TeamOperationResult(True, target.read_text(encoding="utf-8"), metadata={"path": str(target), "workdir": str(member.workdir)})
        if content is None:
            return TeamOperationResult(False, "TeamDispatch 写入模式必须提供 content。")
        target.parent.mkdir(parents=True, exist_ok=True)
        if mode == "append" and target.exists():
            existing = target.read_text(encoding="utf-8")
            separator = "" if existing.endswith("\n") or existing == "" else "\n"
            target.write_text(f"{existing}{separator}{content}", encoding="utf-8")
        else:
            target.write_text(content, encoding="utf-8")
        updated = self.roster.update_status(team, member.name, TeamMemberStatus.IDLE)
        self.store.save(updated)
        return TeamOperationResult(
            True,
            f"队员 {member.name} 已在自己的工作目录执行任务。\n- 工作目录：{member.workdir}\n- 文件：{target}",
            metadata={"agent_id": member.agent_id, "workdir": str(member.workdir), "path": str(target), "task": task},
        )

    def mark_member_idle(self, team_name: str, member_name: str) -> TeamOperationResult:
        team = self.store.load(team_name)
        updated = self.roster.update_status(team, member_name, TeamMemberStatus.IDLE)
        self.store.save(updated)
        return TeamOperationResult(True, f"队员已空闲：{member_name}")

    def poll_notifications(self) -> tuple[str, ...]:
        notifications = tuple(self._notifications)
        self._notifications.clear()
        return notifications


def _resolve_member_file(workdir: Path, relative_path: str) -> Path:
    target = (Path(workdir) / relative_path).resolve()
    root = Path(workdir).resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"队员文件路径越界：{relative_path}")
    return target



def replace_member_status(team: AgentTeam, member_name: str, status: TeamMemberStatus) -> AgentTeam:
    roster = TeamRoster()
    member = roster.get(team, member_name)
    if member is None:
        return team
    return roster.update_member(team, replace(member, status=status))
