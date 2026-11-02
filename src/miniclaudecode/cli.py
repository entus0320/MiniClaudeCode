"""Command line entry point for MiniClaudeCode."""

from __future__ import annotations

import argparse
import platform
import sys
from datetime import date
from pathlib import Path

from miniclaudecode.commands import create_builtin_command_registry
from miniclaudecode.config import ConfigError, load_config
from miniclaudecode.context import ContextManager
from miniclaudecode.errors import safe_message
from miniclaudecode.hooks import HookEngine, HookError, HookLoader, HookRegistry
from miniclaudecode.instructions import load_instruction_bundle
from miniclaudecode.mcp.manager import McpConnectionManager
from miniclaudecode.memory import MemoryManager
from miniclaudecode.policy import POLICY_FILENAME, PolicyConfigError, PolicyContext, PolicyEngine, SessionPolicy, load_project_policy
from miniclaudecode.prompt_modules import default_prompt_modules
from miniclaudecode.prompting import PromptAssembler, RuntimeContext
from miniclaudecode.providers.factory import create_provider
from miniclaudecode.session import Session
from miniclaudecode.session_store import SessionStore
from miniclaudecode.skills import SkillError, SkillLoader, SkillRegistry, SkillRuntime, register_skill_commands
from miniclaudecode.subagents import AgentDefinitionLoader, AgentDefinitionRegistry, SubAgentError
from miniclaudecode.subagents.commands import register_subagent_commands
from miniclaudecode.subagents.runtime import SubAgentRuntime, SubAgentRuntimeDependencies
from miniclaudecode.subagents.tasks import TaskManager
from miniclaudecode.subagents.tool import AgentTool, SubAgentLauncher
from miniclaudecode.subagents.trace import TraceRegistry
from miniclaudecode.tools.base import ToolContext
from miniclaudecode.teams import AgentTeamStore, TeamLifecycleManager
from miniclaudecode.teams.commands import register_team_commands
from miniclaudecode.teams.in_process import InProcessBackend
from miniclaudecode.teams.iterm2 import ITerm2PaneBackend
from miniclaudecode.teams.tmux import TmuxPaneBackend
from miniclaudecode.teams.tools import TeamToolInjector
from miniclaudecode.teams.wakeup import WakeupDispatcher
from miniclaudecode.tools.defaults import create_default_tool_registry
from miniclaudecode.tui import MiniClaudeCodeApp
from miniclaudecode.worktrees import SubAgentWorktreeAdapter, WorktreeCleanupWorker, WorktreeManager, WorktreeStore
from miniclaudecode.worktrees.commands import register_worktree_commands


def main(argv: list[str] | None = None) -> int:
    """Run MiniClaudeCode and return a process exit code."""

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--coordinator-mode", action="store_true")
    parsed, _unknown = parser.parse_known_args(argv or [])
    try:
        app_config = load_config()
        provider_config = app_config.active_provider
        provider = create_provider(provider_config)
        workdir = Path.cwd()
        context_manager = ContextManager(app_config.context_management, workdir=workdir)
        session_store = SessionStore(
            workdir / app_config.sessions.dir,
            provider_name=provider_config.name,
            model=provider_config.model,
            enabled=app_config.sessions.enabled,
        )
        session = Session(tool_result_slimmer=context_manager.tool_result_slimmer, recorder=session_store)
        memory_manager = MemoryManager(
            user_dir=app_config.memory.user_dir,
            project_dir=workdir / app_config.memory.project_dir,
            auto_every_turns=app_config.memory.auto_every_turns,
            recent_messages=app_config.memory.recent_messages,
            enabled=app_config.memory.enabled,
        )
        tool_registry = create_default_tool_registry()
        mcp_manager = McpConnectionManager(app_config.mcp_servers)
        mcp_manager.discover_tools(tool_registry)
        command_registry = create_builtin_command_registry()
        mcp_secrets = [secret for server in app_config.mcp_servers for secret in server.secrets]
        tool_context = ToolContext(
            workdir=workdir,
            secrets=tuple(secret for secret in [provider_config.credential, *mcp_secrets] if secret),
        )
        hook_loader = HookLoader(
            project_root=workdir / ".miniclaudecode" / "hooks",
            user_root=Path("~/.miniclaudecode/hooks"),
        )
        hook_registry = HookRegistry(hook_loader)
        hook_registry.load_initial()
        hook_engine = HookEngine(hook_registry, tool_context=tool_context)
        instruction_bundle = load_instruction_bundle(app_config.instructions, workdir=workdir)
        prompt_modules = list(default_prompt_modules())
        instruction_module = instruction_bundle.to_prompt_module()
        if instruction_module is not None:
            prompt_modules.append(instruction_module)
        memory_module = memory_manager.to_prompt_module()
        if memory_module is not None:
            prompt_modules.append(memory_module)
        prompt_assembler = PromptAssembler(prompt_modules)
        project_policy = load_project_policy(workdir / POLICY_FILENAME)
        policy_context = PolicyContext(
            workdir=workdir,
            permission_mode=project_policy.mode,
            project_rules=project_policy.rules,
            session_policy=SessionPolicy(),
        )
        policy_engine = PolicyEngine()

        def runtime_context_factory() -> RuntimeContext:
            return RuntimeContext(
                workdir=workdir,
                os_name=platform.platform(),
                current_date=date.today().isoformat(),
                provider_name=provider_config.name,
                model=provider_config.model,
                git_status=None,
            )

        worktree_manager = None
        worktree_cleanup = None
        if app_config.worktrees.enabled:
            worktree_store = WorktreeStore(workdir / app_config.worktrees.state_file)
            worktree_state = worktree_store.load(mark_unrecoverable_tasks=parsed.resume)
            worktree_manager = WorktreeManager(
                main_workdir=workdir,
                config=app_config.worktrees,
                store=worktree_store,
                state=worktree_state,
            )
            worktree_cleanup = WorktreeCleanupWorker(worktree_manager)
            register_worktree_commands(command_registry, worktree_manager)

        team_manager = None
        if app_config.teams.enabled:
            team_backends = {}
            for backend in (InProcessBackend(), TmuxPaneBackend(), ITerm2PaneBackend()):
                team_backends[backend.kind] = backend
            team_manager = TeamLifecycleManager(
                AgentTeamStore(workdir / app_config.teams.root_dir, config=app_config.teams),
                wakeup=WakeupDispatcher(team_backends),
                worktree_manager=worktree_manager,
            )
            TeamToolInjector(team_manager).register_top_level(tool_registry)
            register_team_commands(command_registry, team_manager)

        subagent_registry = None
        task_manager = None
        trace_registry = None
        if app_config.subagents.enabled:
            subagent_loader = AgentDefinitionLoader(
                project_root=workdir / app_config.subagents.project_dir,
                user_root=app_config.subagents.user_dir,
                builtin_root=Path(__file__).resolve().parent / "builtin_subagents",
                include_verify=app_config.subagents.include_verify_agent,
            )
            subagent_registry = AgentDefinitionRegistry(subagent_loader, tool_registry=tool_registry)
            subagent_registry.load_initial()
            trace_registry = TraceRegistry()
            task_manager = TaskManager(
                trace_registry=trace_registry,
                default_timeout_seconds=app_config.subagents.background_timeout_seconds,
            )
            subagent_runtime = SubAgentRuntime(
                SubAgentRuntimeDependencies(
                    provider=provider,
                    parent_session=session,
                    tool_registry=tool_registry,
                    tool_context=tool_context,
                    prompt_assembler=prompt_assembler,
                    runtime_context_factory=runtime_context_factory,
                    policy_engine=policy_engine,
                    policy_context=policy_context,
                    context_manager=context_manager,
                    hook_engine=hook_engine,
                    trace_registry=trace_registry,
                    worktree_adapter=SubAgentWorktreeAdapter(worktree_manager) if worktree_manager is not None else None,
                    background_allowed_tools=tuple(app_config.subagents.background_allowed_tools),
                )
            )
            launcher = SubAgentLauncher(
                registry=subagent_registry,
                runtime=subagent_runtime,
                task_manager=task_manager,
            )
            tool_registry.register(AgentTool(launcher))
            register_subagent_commands(command_registry, task_manager, trace_registry)

        skill_loader = SkillLoader(
            project_root=workdir / ".miniclaudecode" / "skills",
            user_root=Path("~/.miniclaudecode/skills"),
            builtin_root=Path(__file__).resolve().parent / "builtin_skills",
        )
        skill_registry = SkillRegistry(skill_loader, tool_registry=tool_registry, command_registry=command_registry)
        skill_registry.load_initial()
        skill_runtime = SkillRuntime(skill_registry)
        register_skill_commands(command_registry, skill_registry, skill_runtime)

        startup_warnings = [
            *mcp_manager.startup_warnings,
            *instruction_bundle.warnings,
            *(warning.render() for warning in skill_registry.warnings),
            *(diagnostic.render() for diagnostic in hook_registry.diagnostics),
        ]
        if subagent_registry is not None:
            startup_warnings.extend(warning.render() for warning in subagent_registry.warnings)

        app = MiniClaudeCodeApp(
            provider,
            session,
            provider_name=provider_config.name,
            model=provider_config.model,
            tool_registry=tool_registry,
            tool_context=tool_context,
            prompt_assembler=prompt_assembler,
            runtime_context_factory=runtime_context_factory,
            policy_engine=policy_engine,
            policy_context=policy_context,
            context_manager=context_manager,
            session_store=session_store,
            memory_manager=memory_manager,
            command_registry=command_registry,
            skill_runtime=skill_runtime,
            hook_engine=hook_engine,
            task_manager=task_manager,
            trace_registry=trace_registry,
            subagent_registry=subagent_registry,
            worktree_manager=worktree_manager,
            worktree_cleanup=worktree_cleanup,
            team_manager=team_manager,
            mcp_manager=mcp_manager,
            startup_warnings=tuple(startup_warnings),
        )
        result = app.run()
        return 0 if result is None else int(result)
    except (ConfigError, PolicyConfigError, SkillError, HookError, SubAgentError) as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        message = safe_message(str(exc)) or "启动失败。"
        print(f"启动失败：{message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
