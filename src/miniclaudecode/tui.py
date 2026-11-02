"""Textual TUI for MiniClaudeCode."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import Future
from pathlib import Path
from typing import Any

from rich.markup import escape
from textual import events
from textual.app import App, ComposeResult
from textual.containers import Container, VerticalScroll
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option
from textual.worker import Worker

from miniclaudecode import __version__
from miniclaudecode.commands import (
    CommandCompletionItem,
    CommandContext,
    CommandParser,
    CommandRegistry,
    CommandResult,
    CommandType,
    create_builtin_command_registry,
)
from miniclaudecode.context import ContextManagementResult, ContextManager
from miniclaudecode.hooks import HookConfirmationRequest, HookContext, HookDiagnostic, HookEngine, HookEventName
from miniclaudecode.observability import GenerationWatchConfig, GenerationWatchEvent, GenerationWatchdog
from miniclaudecode.agent import (
    AgentCallbacks,
    AgentCancelled,
    AgentContextManagementEvent,
    AgentError,
    AgentFinalResponse,
    AgentHookDiagnosticEvent,
    AgentMaxRoundsReached,
    AgentOptions,
    AgentRunner,
    AgentTextDelta,
    AgentToolCallStarted,
    AgentToolResultOffloaded,
    ConfirmationResponse,
)
from miniclaudecode.policy import PolicyContext, PolicyDecision, PolicyEngine
from miniclaudecode.prompting import PromptAssembler, RuntimeContext
from miniclaudecode.providers.base import BaseProvider, ProviderError
from miniclaudecode.memory import MemoryManager, MemoryUpdateResult
from miniclaudecode.session import Message, Session, ToolResultOffloadInfo
from miniclaudecode.session_store import SessionStore
from miniclaudecode.skills.runtime import SkillRuntime
from miniclaudecode.subagents.registry import AgentDefinitionRegistry
from miniclaudecode.subagents.tasks import TaskManager
from miniclaudecode.subagents.trace import TraceRegistry
from miniclaudecode.teams.lifecycle import TeamLifecycleManager
from miniclaudecode.tools.base import Tool, ToolCall, ToolContext
from miniclaudecode.tools.registry import ToolRegistry
from miniclaudecode.worktrees.cleanup import WorktreeCleanupWorker
from miniclaudecode.worktrees.manager import WorktreeManager

WELCOME_WIDTH = 64
WELCOME_AVATAR = r"""
     /\_/\
    ( o.o )
     > ^ <
  ⚡ PIKA! ⚡
""".strip("\n")

_CONFIRMATION_CHOICE_RESPONSES = {
    "allow_once": ConfirmationResponse.ALLOW_ONCE,
    "allow_session": ConfirmationResponse.ALLOW_SESSION,
    "deny": ConfirmationResponse.DENY,
}
_CONFIRMATION_INPUTS = {
    "1": "allow_once",
    "2": "allow_session",
    "3": "deny",
    "/allow": "allow_once",
    "/allow-session": "allow_session",
    "/deny": "deny",
}
_CONFIRMATION_MESSAGES = {
    "allow_once": "已允许本次工具调用。",
    "allow_session": "已允许本会话内同类工具调用。",
    "deny": "已拒绝执行工具。",
}


class MiniClaudeCodeApp(App[int]):
    """Minimal terminal UI for streaming chat."""

    BINDINGS = [("ctrl+c", "interrupt", "Interrupt")]
    CSS = """
    Screen {
        background: $background;
    }

    #main {
        height: 1fr;
    }

    #message-scroll {
        height: 1fr;
        border: round $accent;
        padding: 1 2;
    }

    #message-history {
        width: 100%;
        height: auto;
        content-align: left top;
    }

    #message-history.welcome {
        height: 100%;
        content-align: center middle;
    }

    #confirmation-menu {
        height: auto;
        max-height: 5;
        border: round $warning;
        margin-top: 1;
    }

    #command-completion-menu {
        height: auto;
        max-height: 8;
        border: round $accent;
        margin-top: 1;
    }

    #input-box {
        dock: bottom;
        border: tall $primary;
    }

    #status-bar {
        dock: bottom;
        height: 1;
        background: $surface;
        color: $text-muted;
        text-align: center;
    }
    """

    def __init__(
        self,
        provider: BaseProvider,
        session: Session,
        *,
        provider_name: str,
        model: str,
        cwd: Path | None = None,
        tool_registry: ToolRegistry | None = None,
        tool_context: ToolContext | None = None,
        prompt_assembler: PromptAssembler | None = None,
        runtime_context_factory: Callable[[], RuntimeContext] | None = None,
        policy_engine: PolicyEngine | None = None,
        policy_context: PolicyContext | None = None,
        context_manager: ContextManager | None = None,
        session_store: SessionStore | None = None,
        memory_manager: MemoryManager | None = None,
        command_registry: CommandRegistry | None = None,
        skill_runtime: SkillRuntime | None = None,
        generation_watch_config: GenerationWatchConfig | None = None,
        hook_engine: HookEngine | None = None,
        task_manager: TaskManager | None = None,
        trace_registry: TraceRegistry | None = None,
        subagent_registry: AgentDefinitionRegistry | None = None,
        worktree_manager: WorktreeManager | None = None,
        worktree_cleanup: WorktreeCleanupWorker | None = None,
        team_manager: TeamLifecycleManager | None = None,
        mcp_manager: Any | None = None,
        startup_warnings: Sequence[str] = (),
    ) -> None:
        super().__init__()
        self.provider = provider
        self.session = session
        self.provider_name = provider_name
        self.model = model
        self.cwd = Path.cwd() if cwd is None else cwd
        self.tool_registry = tool_registry
        self.tool_context = tool_context
        self.prompt_assembler = prompt_assembler
        self.runtime_context_factory = runtime_context_factory
        self.policy_engine = policy_engine
        self.policy_context = policy_context
        self.context_manager = context_manager
        self.session_store = session_store
        self.memory_manager = memory_manager
        self.command_registry = command_registry or create_builtin_command_registry()
        self.command_parser = CommandParser()
        self.skill_runtime = skill_runtime
        self.generation_watch_config = generation_watch_config or GenerationWatchConfig()
        self.hook_engine = hook_engine
        if self.hook_engine is not None:
            self.hook_engine.set_confirm_action(self._confirm_hook_action)
            self.hook_engine.set_diagnostic_callback(lambda diagnostic: self.call_from_thread(self._append_hook_diagnostic, diagnostic))
        self.task_manager = task_manager
        self.trace_registry = trace_registry
        self.subagent_registry = subagent_registry
        self.worktree_manager = worktree_manager
        self.worktree_cleanup = worktree_cleanup
        if self.worktree_manager is not None:
            self.worktree_manager.set_switch_callback(self._switch_workdir)
        self.team_manager = team_manager
        self.mcp_manager = mcp_manager
        self.startup_warnings = tuple(startup_warnings)
        self.status = "Ready"
        self.messages: list[tuple[str, str]] = []
        self._assistant_buffer = ""
        self._command_completion_visible = False
        self._current_worker: Worker[Any] | None = None
        self._pending_confirmation: Future[ConfirmationResponse] | None = None
        self._cancel_requested = False
        self._plain_generation_watchdog: GenerationWatchdog | None = None
        self._active_agent_runner: AgentRunner | None = None
        self._watch_status_suffix: str | None = None

    def compose(self) -> ComposeResult:
        yield Container(
            VerticalScroll(Static("", id="message-history"), id="message-scroll"),
            OptionList(id="confirmation-menu"),
            OptionList(id="command-completion-menu"),
            id="main",
        )
        yield Input(placeholder="输入消息，或输入 /exit 退出", id="input-box")
        yield Static(self.status_text, id="status-bar")

    def on_mount(self) -> None:
        self._hide_confirmation_menu()
        self._hide_command_completion_menu()
        self._refresh_messages()
        for warning in self.startup_warnings:
            self._append_system_message(warning)
        self._trigger_app_hook(HookEventName.SYSTEM_START)
        self._trigger_app_hook(HookEventName.SESSION_START)
        self.set_interval(1.0, self._poll_generation_watchdogs)
        if self.task_manager is not None:
            self.set_interval(1.0, self._poll_subagent_notifications)
        if self.worktree_cleanup is not None:
            self.set_interval(1.0, self._poll_worktree_cleanup)
        if self.team_manager is not None:
            self.set_interval(1.0, self._poll_team_notifications)
        self._set_status("Ready")

    def on_unmount(self) -> None:
        self._trigger_app_hook(HookEventName.SESSION_END)
        self._trigger_app_hook(HookEventName.SYSTEM_EXIT)
        if self.memory_manager is not None and (self._current_worker is None or self._current_worker.is_finished):
            self._save_notes_now("exit")
        if self.mcp_manager is not None:
            self.mcp_manager.close_all()

    def on_key(self, event: events.Key) -> None:
        if event.key == "tab":
            if self._handle_command_completion_tab():
                event.prevent_default()
                event.stop()
            return
        if self._pending_confirmation is None or self._pending_confirmation.done():
            return
        choice_id = _CONFIRMATION_INPUTS.get(event.key)
        if choice_id is None:
            return
        event.prevent_default()
        event.stop()
        self._resolve_pending_confirmation(choice_id)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        option_id = event.option.id
        if option_id is None:
            return
        option_list_id = event.option_list.id
        if option_list_id == "confirmation-menu":
            self._resolve_pending_confirmation(str(option_id))
        elif option_list_id == "command-completion-menu":
            self._accept_command_completion(str(option_id))

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "input-box":
            return
        prefix = self.command_parser.completion_prefix(event.value)
        if prefix is None:
            self._hide_command_completion_menu()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.input.value = ""
        self._hide_command_completion_menu()
        value = event.value.strip()
        if value == "":
            return
        invocation = self.command_parser.parse(value)
        if invocation is not None:
            self._dispatch_command(invocation)
            return
        if self._pending_confirmation is not None and not self._pending_confirmation.done():
            choice_id = _CONFIRMATION_INPUTS.get(value)
            if choice_id is not None:
                self._resolve_pending_confirmation(choice_id)
            else:
                self._append_system_message("当前正在等待工具确认，请选择菜单项，或输入 1、2、3。")
            return
        if self._current_worker is not None and not self._current_worker.is_finished:
            self._append_system_message("正在生成，请等待完成或输入 /interrupt 中断。")
            return
        self.submit_user_message(value)

    def _dispatch_command(self, invocation) -> None:
        command = self.command_registry.get(invocation.name)
        if command is None:
            self._append_system_message(_format_unknown_command(invocation.name, self.command_registry.suggestions(invocation.name)))
            return
        if self.has_pending_confirmation() and not command.allowed_during_confirmation:
            self._append_system_message("当前正在等待工具确认，请选择菜单项，或输入 1、2、3。")
            return
        if self.is_generating() and not command.allowed_while_generating:
            self._append_system_message("正在生成，请等待完成或输入 /interrupt 中断。")
            return
        if command.type is CommandType.PROMPT:
            self._append_system_message("AI prompt 类型命令暂未启用。")
            return
        if command.handler is None:
            self._append_system_message(f"命令暂不可用：/{command.name}")
            return
        context = CommandContext(ui=self, registry=self.command_registry, app=self)
        try:
            result = command.handler(context, invocation) or CommandResult()
        except Exception as exc:
            self._append_system_message(f"命令执行失败：/{command.name}\n- 详情：{exc}")
            return
        if result.message:
            self._append_system_message(result.message)

    def append_system_message(self, content: str) -> None:
        self._append_system_message(content)

    def _switch_workdir(self, workdir: Path) -> None:
        self.cwd = Path(workdir).resolve()
        if self.tool_context is not None:
            self.tool_context = ToolContext(
                self.cwd,
                command_timeout_seconds=self.tool_context.command_timeout_seconds,
                max_read_bytes=self.tool_context.max_read_bytes,
                max_output_chars=self.tool_context.max_output_chars,
                max_results=self.tool_context.max_results,
                secrets=self.tool_context.secrets,
            )
        if self.policy_context is not None:
            self.policy_context = PolicyContext(
                workdir=self.cwd,
                permission_mode=self.policy_context.permission_mode,
                project_rules=self.policy_context.project_rules,
                plan_only=self.policy_context.plan_only,
            )
        self._append_system_message(f"工作目录已切换：{self.cwd}")

    def exit_app(self, code: int = 0) -> None:
        self.exit(code)

    def interrupt(self) -> None:
        self.action_interrupt()

    def is_generating(self) -> bool:
        return self._current_worker is not None and not self._current_worker.is_finished

    def has_pending_confirmation(self) -> bool:
        return self._pending_confirmation is not None and not self._pending_confirmation.done()

    def resolve_confirmation_command(self, choice_id: str) -> bool:
        return self._resolve_pending_confirmation(choice_id)

    def submit_user_message(self, content: str) -> None:
        """Append a user message and start provider streaming."""

        self._trigger_app_hook(
            HookEventName.MESSAGE_BEFORE_SEND,
            HookContext(
                event=HookEventName.MESSAGE_BEFORE_SEND,
                workdir=self.cwd,
                message={"role": "user", "content": content},
            ),
        )
        self.session.add_user_message(content)
        self.messages.append(("User", content))
        self._assistant_buffer = ""
        self.messages.append(("Assistant", ""))
        self._cancel_requested = False
        self._refresh_messages()
        self._set_status("Generating...")
        history = self.session.get_history()
        self._current_worker = self.run_worker(
            lambda: self._run_generation(history),
            thread=True,
            exit_on_error=False,
        )

    def _handle_compact_command(self) -> None:
        if self._pending_confirmation is not None and not self._pending_confirmation.done():
            self._append_system_message("当前正在等待工具确认，请先处理确认后再 /compact。")
            return
        if self._current_worker is not None and not self._current_worker.is_finished:
            self._append_system_message("正在生成，请等待完成或输入 /interrupt 中断后再 /compact。")
            return
        if self.context_manager is None:
            self._append_system_message("上下文管理未启用，无法执行 /compact。")
            return
        if not self.session.get_history():
            self._append_system_message("当前没有可压缩的对话历史。")
            return
        self._set_status("Compacting...")
        self._current_worker = self.run_worker(
            self._run_manual_compact,
            thread=True,
            exit_on_error=False,
        )

    def _handle_command_completion_tab(self) -> bool:
        if self.has_pending_confirmation():
            self._hide_command_completion_menu()
            return False
        input_box = self.query_one("#input-box", Input)
        prefix = self.command_parser.completion_prefix(input_box.value)
        if prefix is None:
            self._hide_command_completion_menu()
            return False
        completions = self.command_registry.completions(prefix)
        if not completions:
            self._hide_command_completion_menu()
            return True
        if len(completions) == 1:
            self._accept_command_completion(completions[0].value)
            return True
        self._show_command_completions(completions)
        return True

    def _show_command_completions(self, completions: Sequence[CommandCompletionItem]) -> None:
        menu = self.query_one("#command-completion-menu", OptionList)
        menu.clear_options()
        menu.add_options([Option(_format_command_completion(item), id=item.value) for item in completions])
        menu.display = True
        self._command_completion_visible = True
        self._set_status(self.status)
        menu.focus()

    def _accept_command_completion(self, value: str) -> None:
        input_box = self.query_one("#input-box", Input)
        input_box.value = f"{value} "
        input_box.cursor_position = len(input_box.value)
        self._hide_command_completion_menu()
        input_box.focus()

    def _hide_command_completion_menu(self) -> None:
        menu = self.query_one("#command-completion-menu", OptionList)
        was_visible = self._command_completion_visible
        menu.display = False
        menu.clear_options()
        self._command_completion_visible = False
        if was_visible:
            self._set_status(self.status)

    def _handle_session_command(self) -> None:
        if self.session_store is None:
            self._append_system_message("会话存档未启用。")
            return
        meta = self.session_store._meta
        self._append_system_message(
            "当前会话：\n"
            f"- ID：{meta.session_id}\n"
            f"- 路径：{self.session_store.session_dir}\n"
            f"- 消息数：{len(self.session.get_history())}\n"
            f"- 事件数：{meta.event_count}"
        )

    def _handle_sessions_command(self) -> None:
        if self.session_store is None:
            self._append_system_message("会话存档未启用。")
            return
        sessions = self.session_store.list_sessions()
        if not sessions:
            self._append_system_message("暂无历史会话。")
            return
        lines = ["最近会话："]
        for meta in sessions:
            lines.append(f"- {meta.session_id} | {meta.updated_at} | {meta.title} | {meta.message_count} messages")
        self._append_system_message("\n".join(lines))

    def _handle_resume_command(self, session_id: str) -> None:
        if self._current_worker is not None and not self._current_worker.is_finished:
            self._append_system_message("正在生成，请等待完成或输入 /interrupt 中断后再恢复会话。")
            return
        if not session_id.strip():
            self._append_system_message("用法：/resume <session_id>")
            return
        if self.session_store is None:
            self._append_system_message("会话存档未启用。")
            return
        loaded = self.session_store.load_session(session_id.strip())
        if self.skill_runtime is not None:
            self.skill_runtime.clear_active()
        self.session.replace_history(loaded.messages)
        self.messages = _display_messages_from_history(loaded.messages)
        self._refresh_messages()
        warning_text = "\n".join(f"- {warning}" for warning in loaded.warnings)
        suffix = f"\n恢复警告：\n{warning_text}" if warning_text else ""
        self._append_system_message(f"已恢复会话：{loaded.meta.session_id}，消息数 {len(loaded.messages)}。{suffix}")

    def _handle_notes_command(self) -> None:
        if self.memory_manager is None:
            self._append_system_message("自动笔记未启用。")
            return
        paths = self.memory_manager.note_paths()
        lines = ["笔记文件："]
        for name, path in paths.items():
            status = "存在" if path.exists() else "未创建"
            lines.append(f"- {name}: {path} ({status})")
        self._append_system_message("\n".join(lines))

    def _handle_notes_path_command(self) -> None:
        if self.memory_manager is None:
            self._append_system_message("自动笔记未启用。")
            return
        self._append_system_message(
            "笔记目录：\n"
            f"- 用户级：{self.memory_manager.user_dir}\n"
            f"- 项目级：{self.memory_manager.project_dir}"
        )

    def _handle_notes_save_command(self) -> None:
        if self._current_worker is not None and not self._current_worker.is_finished:
            self._append_system_message("正在生成，请等待完成或输入 /interrupt 中断后再保存笔记。")
            return
        result = self._save_notes_now("manual")
        self._append_system_message(_format_memory_result(result))

    def _save_notes_now(self, reason: str) -> MemoryUpdateResult:
        if self.memory_manager is None:
            return MemoryUpdateResult(updated=False, reason=reason, error="自动笔记未启用。")
        return self.memory_manager.update_now(self.provider, self.session, reason=reason)

    def _record_completed_user_turn_from_worker(self) -> None:
        if self.memory_manager is None:
            return
        result = self.memory_manager.record_user_turn(self.provider, self.session)
        if result is not None:
            self.call_from_thread(self._append_system_message, _format_memory_result(result))

    def action_interrupt(self) -> None:
        """Best-effort interruption for the current generation."""

        if self._current_worker is None or self._current_worker.is_finished:
            return
        self._cancel_requested = True
        if self._pending_confirmation is not None and not self._pending_confirmation.done():
            self._resolve_pending_confirmation("deny")
        self._current_worker.cancel()
        self._append_system_message("本轮生成已中断，临时回复未保存到历史。")
        self._set_status("Interrupted")

    def _run_generation(self, history: list[Message]) -> None:
        if self.tool_registry is not None and self.tool_context is not None:
            self._run_generation_with_tools(history)
            return

        completed = False
        try:
            prompt = self._assemble_prompt()
            request_history = list(history)
            if self.context_manager is not None:
                context_result = self.context_manager.prepare_before_request(
                    session=self.session,
                    provider=self.provider,
                    tools=[],
                    prompt=prompt,
                    allow_auto_compact=True,
                )
                if _should_surface_context_result(context_result):
                    self.call_from_thread(
                        self._append_system_message,
                        _format_context_management_notice(context_result, automatic=True),
                    )
                request_history = self.session.get_history()
            stream = (
                self.provider.stream_chat(request_history, prompt=prompt)
                if prompt is not None
                else self.provider.stream_chat(request_history)
            )
            self._plain_generation_watchdog = GenerationWatchdog(
                self.generation_watch_config,
                phase="plain_chat",
                label="普通对话生成",
            )
            for delta in stream:
                if self._cancel_requested:
                    return
                if self._plain_generation_watchdog is not None:
                    self._plain_generation_watchdog.mark_activity()
                self._assistant_buffer += delta
                self.call_from_thread(self._update_assistant_message, self._assistant_buffer)
            completed = True
        except ProviderError as exc:
            self.call_from_thread(
                self._finish_failed_generation,
                f"错误：{exc} 本轮 assistant 回复未保存到历史。",
            )
        except Exception:
            self.call_from_thread(
                self._finish_failed_generation,
                "错误：生成失败。本轮 assistant 回复未保存到历史。",
            )
        finally:
            if self._plain_generation_watchdog is not None:
                self._plain_generation_watchdog.finish()
                self._plain_generation_watchdog = None
                self.call_from_thread(self._clear_generation_watch_status)
            if completed and not self._cancel_requested:
                self.call_from_thread(self._finish_successful_generation, self._assistant_buffer)
                self._record_completed_user_turn_from_worker()

    def _run_manual_compact(self) -> None:
        try:
            if self.context_manager is None:
                self.call_from_thread(self._append_system_message, "上下文管理未启用，无法执行 /compact。")
                return
            result = self.context_manager.manual_compact(session=self.session, provider=self.provider)
            self.call_from_thread(
                self._append_system_message,
                _format_context_management_notice(result, automatic=False),
            )
        finally:
            self.call_from_thread(self._set_status, "Ready")

    def _run_generation_with_tools(self, history: list[Message]) -> None:
        try:
            callbacks = AgentCallbacks(
                on_status=self._on_agent_status,
                confirm_tool=self._confirm_tool,
            )
            runner = AgentRunner(
                self.provider,
                self.session,
                self.tool_registry,
                self.tool_context,
                callbacks,
                AgentOptions(is_cancelled=lambda: self._cancel_requested),
                prompt_assembler=self.prompt_assembler,
                runtime_context_factory=self.runtime_context_factory,
                policy_engine=self.policy_engine,
                policy_context=self.policy_context,
                context_manager=self.context_manager,
                skill_runtime=self.skill_runtime,
                generation_watch_config=self.generation_watch_config,
                hook_engine=self.hook_engine,
            )
            self._active_agent_runner = runner
            for event in runner.stream(history):
                if isinstance(event, AgentTextDelta):
                    self._handle_agent_text_delta(event.text)
                elif isinstance(event, AgentToolCallStarted):
                    self.call_from_thread(self._append_tool_message, event.call)
                elif isinstance(event, AgentToolResultOffloaded):
                    self.call_from_thread(
                        self._append_system_message,
                        _format_tool_offload_notice(event.offload),
                    )
                elif isinstance(event, AgentContextManagementEvent):
                    self.call_from_thread(
                        self._append_system_message,
                        _format_context_management_notice(event.result, automatic=event.automatic),
                    )
                elif isinstance(event, AgentHookDiagnosticEvent):
                    self.call_from_thread(self._append_hook_diagnostic, event.diagnostic)
                elif isinstance(event, AgentFinalResponse):
                    self.call_from_thread(self._update_assistant_message, event.text)
                    self.call_from_thread(self._set_status, "Ready")
                    self._record_completed_user_turn_from_worker()
                elif isinstance(event, AgentMaxRoundsReached):
                    self.call_from_thread(self._update_assistant_message, event.message)
                    self.call_from_thread(self._set_status, "Ready")
                elif isinstance(event, AgentCancelled):
                    self.call_from_thread(self._append_system_message, event.message)
                    self.call_from_thread(self._set_status, "Interrupted")
                elif isinstance(event, AgentError):
                    self.call_from_thread(self._finish_failed_generation, event.message)
        except ProviderError as exc:
            self.call_from_thread(
                self._finish_failed_generation,
                f"错误：{exc} 本轮 assistant 回复未保存到历史。",
            )
        except Exception:
            self.call_from_thread(
                self._finish_failed_generation,
                "错误：工具调用流程失败。本轮 assistant 回复未保存到历史。",
            )
        finally:
            self._active_agent_runner = None

    def _poll_generation_watchdogs(self) -> None:
        events: list[GenerationWatchEvent] = []
        if self._plain_generation_watchdog is not None:
            events.extend(self._plain_generation_watchdog.poll())
        if self._active_agent_runner is not None:
            events.extend(self._active_agent_runner.poll_generation_watchdog())
        for event in events:
            self._handle_generation_watch_event(event)

    def _poll_subagent_notifications(self) -> None:
        if self.task_manager is None or self.is_generating() or self.has_pending_confirmation():
            return
        for notification in self.task_manager.poll_notifications():
            self._append_system_message(notification.render())

    def _poll_worktree_cleanup(self) -> None:
        if self.worktree_cleanup is None or self.is_generating() or self.has_pending_confirmation():
            return
        for diagnostic in self.worktree_cleanup.tick():
            self._append_system_message(diagnostic.render())

    def _poll_team_notifications(self) -> None:
        if self.team_manager is None or self.is_generating() or self.has_pending_confirmation():
            return
        for notification in self.team_manager.poll_notifications():
            self._append_system_message(notification)

    def _handle_generation_watch_event(self, event: GenerationWatchEvent) -> None:
        if event.kind == "idle_timeout":
            self._watch_status_suffix = f"已 {_format_seconds(event.idle_for_seconds)} 无模型输出"
            self._append_system_message(
                f"观测提示：{event.label}已连续 {_format_seconds(event.idle_for_seconds)} 没有模型输出，"
                "可能卡在等待模型生成。当前仅观测，不会自动中断。"
            )
        elif event.kind == "total_timeout":
            self._watch_status_suffix = f"总耗时已 {_format_seconds(event.elapsed_seconds)}"
            self._append_system_message(
                f"观测提示：{event.label}总耗时已达到 {_format_seconds(event.elapsed_seconds)}。"
                "当前仅观测，不会自动中断。"
            )
        elif event.kind == "recovered":
            self._watch_status_suffix = None
            self._append_system_message(f"观测提示：{event.label}的模型输出已恢复。")
        self._set_status(self.status)

    def _clear_generation_watch_status(self) -> None:
        self._watch_status_suffix = None
        self._set_status(self.status)

    def _assemble_prompt(self):
        if self.prompt_assembler is None:
            return None
        runtime_context = self.runtime_context_factory() if self.runtime_context_factory is not None else None
        dynamic_instructions = []
        if self.skill_runtime is not None:
            dynamic_instructions.extend(self.skill_runtime.dynamic_instructions())
        if self.hook_engine is not None:
            dynamic_instructions.extend(self.hook_engine.consume_dynamic_instructions())
        return self.prompt_assembler.assemble(
            runtime_context=runtime_context,
            dynamic_instructions=dynamic_instructions,
        )

    def _handle_agent_text_delta(self, delta: str) -> None:
        if self._cancel_requested:
            return
        self._assistant_buffer += delta
        self.call_from_thread(self._update_assistant_message, self._assistant_buffer)

    def _on_agent_status(self, status: str) -> None:
        self.call_from_thread(self._set_status, status)

    def _confirm_tool(
        self,
        call: ToolCall,
        tool: Tool,
        decision: PolicyDecision | None = None,
    ) -> ConfirmationResponse:
        future: Future[ConfirmationResponse] = Future()
        summary = tool.summarize_arguments(call.arguments)
        reason = decision.reason if decision is not None else "该工具需要用户确认。"
        self.call_from_thread(
            self._ask_tool_confirmation,
            future,
            (
                f"工具需要确认：{tool.name} {summary}\n"
                f"原因：{reason}\n"
                "请选择菜单项，或按 1/2/3。"
            ),
        )
        return future.result()

    def _confirm_hook_action(self, request: HookConfirmationRequest) -> bool:
        future: Future[ConfirmationResponse] = Future()
        self.call_from_thread(
            self._ask_tool_confirmation,
            future,
            (
                f"Hook 动作需要确认：{request.rule_name}\n"
                f"事件：{request.event.value}\n"
                f"动作：{request.action_type.value}\n"
                f"摘要：{request.summary}\n"
                f"详情：{request.detail}\n"
                "请选择菜单项，或按 1/2/3。"
            ),
        )
        return future.result() is not ConfirmationResponse.DENY

    def _ask_tool_confirmation(self, future: Future[ConfirmationResponse], message: str) -> None:
        self._pending_confirmation = future
        self._append_system_message(
            f"{message}\n\n"
            "1. Yes\n"
            "2. Yes, and don't ask again for this pattern\n"
            "3. No"
        )
        menu = self.query_one("#confirmation-menu", OptionList)
        menu.clear_options()
        menu.add_options(
            [
                Option("1. Yes", id="allow_once"),
                Option("2. Yes, and don't ask again for this pattern", id="allow_session"),
                Option("3. No", id="deny"),
            ]
        )
        menu.display = True
        menu.focus()
        self._set_status(self.status)

    def _resolve_pending_confirmation(self, choice_id: str) -> bool:
        if self._pending_confirmation is None or self._pending_confirmation.done():
            return False
        response = _CONFIRMATION_CHOICE_RESPONSES.get(choice_id)
        if response is None:
            return False
        future = self._pending_confirmation
        future.set_result(response)
        self._pending_confirmation = None
        self._hide_confirmation_menu()
        self._append_system_message(_CONFIRMATION_MESSAGES[choice_id])
        self.query_one("#input-box", Input).focus()
        self._set_status(self.status)
        return True

    def _hide_confirmation_menu(self) -> None:
        menu = self.query_one("#confirmation-menu", OptionList)
        menu.display = False
        menu.clear_options()

    def _finish_successful_generation(self, content: str) -> None:
        self.session.add_assistant_message(content)
        self._update_assistant_message(content)
        self._trigger_app_hook(
            HookEventName.MESSAGE_AFTER_RECEIVE,
            HookContext(
                event=HookEventName.MESSAGE_AFTER_RECEIVE,
                workdir=self.cwd,
                message={"role": "assistant", "content": content},
            ),
        )
        self._set_status("Ready")

    def _finish_failed_generation(self, message: str) -> None:
        self._append_system_message(message)
        self._set_status("Ready")

    def _update_assistant_message(self, content: str) -> None:
        if not self.messages or self.messages[-1][0] != "Assistant":
            self.messages.append(("Assistant", content))
        else:
            self.messages[-1] = ("Assistant", content)
        self._refresh_messages()

    def _append_system_message(self, content: str) -> None:
        self.messages.append(("System", content))
        self._refresh_messages()

    def _append_hook_diagnostic(self, diagnostic: HookDiagnostic) -> None:
        self._append_system_message(diagnostic.render())

    def _trigger_app_hook(self, event: HookEventName, context: HookContext | None = None) -> None:
        if self.hook_engine is None:
            return
        hook_context = context or HookContext(event=event, workdir=self.cwd)
        result = self.hook_engine.trigger(event, hook_context)
        for diagnostic in result.diagnostics:
            self._append_hook_diagnostic(diagnostic)

    def _append_tool_message(self, call: ToolCall) -> None:
        if self.messages and self.messages[-1] == ("Assistant", ""):
            self.messages.pop()
        self.messages.append(("Tool", _format_tool_call(call)))
        self._refresh_messages()

    def _refresh_messages(self) -> None:
        history = self.query_one("#message-history", Static)
        if not self.messages:
            history.add_class("welcome")
            history.update(self.welcome_text)
            self._scroll_history_to_home()
            return

        should_scroll_to_end = self._history_is_at_bottom()
        history.remove_class("welcome")
        rendered = "\n\n".join(_format_message(role, content) for role, content in self.messages)
        history.update(rendered)
        if should_scroll_to_end:
            self._scroll_history_to_end()

    def _history_is_at_bottom(self) -> bool:
        scroll = self.query_one("#message-scroll", VerticalScroll)
        return scroll.scroll_y >= max(0, scroll.max_scroll_y - 1)

    def _scroll_history_to_end(self) -> None:
        scroll = self.query_one("#message-scroll", VerticalScroll)
        self.call_after_refresh(scroll.scroll_end, animate=False)

    def _scroll_history_to_home(self) -> None:
        scroll = self.query_one("#message-scroll", VerticalScroll)
        self.call_after_refresh(scroll.scroll_home, animate=False)

    @property
    def welcome_text(self) -> str:
        lines = [
            f"MiniClaudeCode v{__version__}",
            "",
            "Welcome back!",
            "",
            *WELCOME_AVATAR.splitlines(),
            "",
            f"{self.model} | {self.provider_name}",
            str(self.cwd),
        ]
        return "\n".join(_center_line(line) for line in lines)

    @property
    def status_text(self) -> str:
        status = self.status
        if self._watch_status_suffix:
            status = f"{status} | {self._watch_status_suffix}"
        return f"{self.model} | {self.provider_name} | mode: {self.ui_mode} | {status} | hints: /help /compact /interrupt | {self.cwd}"

    @property
    def ui_mode(self) -> str:
        if self.has_pending_confirmation():
            return "Confirm"
        if self._command_completion_visible:
            return "Command"
        if self.is_generating():
            return "Generating"
        return "Chat"

    def _set_status(self, status: str) -> None:
        self.status = status
        self.query_one("#status-bar", Static).update(self.status_text)


def _format_seconds(seconds: float) -> str:
    if seconds >= 10:
        return f"{seconds:.0f} 秒"
    return f"{seconds:.1f} 秒"


def _format_unknown_command(name: str, suggestions: Sequence[str]) -> str:
    display_name = f"/{name}" if name else "/"
    message = f"未知命令：{display_name}。输入 /help 查看可用命令。"
    if suggestions:
        message += f"\n你是否想输入：{', '.join(suggestions)}"
    return message


def _format_command_completion(item: CommandCompletionItem) -> str:
    usage = item.value
    if item.argument_hint and not item.is_alias:
        usage = f"{usage} {item.argument_hint}"
    if item.is_alias:
        usage = f"{usage} → /{item.command_name}"
    return f"{usage}  {item.description}"


def _should_surface_context_result(result: ContextManagementResult) -> bool:
    return result.compacted or result.error is not None or result.reason in {"summary_failed", "auto_compact_fused"}


def _format_context_management_notice(result: ContextManagementResult, *, automatic: bool) -> str:
    if result.compacted:
        prefix = "上下文接近上限，已自动压缩" if automatic else "上下文压缩完成"
        parts = [f"{prefix}：旧历史已替换为结构化摘要，保留 {result.retained_message_count} 条原始消息。"]
        token_line = _format_token_reduction(result)
        if token_line:
            parts.append(token_line)
        return "\n".join(parts)
    if result.error:
        return _format_compaction_error(result.error, result.reason)
    return _format_compaction_skip_reason(result.reason)


def _format_token_reduction(result: ContextManagementResult) -> str:
    if result.before_estimated_tokens is None or result.after_estimated_tokens is None:
        return ""
    if result.released_estimated_tokens is None:
        return f"- 估算 tokens：{result.before_estimated_tokens} → {result.after_estimated_tokens}"
    return (
        f"- 估算 tokens：{result.before_estimated_tokens} → {result.after_estimated_tokens}"
        f"，释放约 {result.released_estimated_tokens} tokens"
    )


def _format_tool_offload_notice(info: ToolResultOffloadInfo) -> str:
    lines = [
        f"工具结果已保存到 disk：{info.tool_name}",
        f"- 路径：{info.path}",
        f"- 释放约 {info.released_chars} chars / {info.released_estimated_tokens} tokens",
    ]
    preview = _shorten_preview(info.error_preview or info.content_preview)
    if preview:
        lines.append(f"- 预览：{preview}")
    return "\n".join(lines)


def _format_compaction_error(error: str | None, reason: str) -> str:
    friendly = _friendly_compaction_error(error, reason)
    if error and error not in friendly:
        return f"上下文压缩失败：{friendly}\n- 详情：{error}\n- 原始历史已保留，可稍后重试 /compact。"
    return f"上下文压缩失败：{friendly}\n- 原始历史已保留，可稍后重试 /compact。"


def _friendly_compaction_error(error: str | None, reason: str) -> str:
    text = error or reason
    if "formal_summary" in text or "结构化摘要" in text or "固定栏目" in text:
        return "模型没有返回可用的结构化摘要。"
    if "截断" in text or "max_tokens" in text or "length" in text:
        return "摘要输出被截断。"
    if "空文本" in text or "没有返回结果" in text:
        return "摘要调用返回空文本。"
    if "工具调用" in text:
        return "摘要模型尝试调用工具，已拒绝执行。"
    if reason == "auto_compact_fused":
        return "自动压缩连续失败，已暂停自动压缩。"
    return text


def _format_compaction_skip_reason(reason: str) -> str:
    if reason == "not_enough_history":
        return "当前历史太短，暂无可安全压缩的旧消息。"
    if reason == "no_safe_compaction_boundary":
        return "当前没有安全的压缩边界，请等待本轮工具调用完成或继续对话后再试。"
    if reason == "disabled":
        return "上下文管理未启用，无法压缩。"
    return f"当前无需压缩：{reason}"


def _shorten_preview(text: str, limit: int = 500) -> str:
    normalized = text.strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit] + "..."


def stream_to_text(provider: BaseProvider, messages: list[Message]) -> str:
    """Collect provider deltas into one text value for small tests."""

    return "".join(_iter_text(provider, messages))


def _iter_text(provider: BaseProvider, messages: list[Message]) -> Iterator[str]:
    yield from provider.stream_chat(messages)


def _format_message(role: str, content: str) -> str:
    if role == "Tool":
        return f"[green]●[/green] {escape(content)}"
    return f"{escape(role)}: {escape(content)}"


def _display_messages_from_history(messages: Sequence[Message]) -> list[tuple[str, str]]:
    rendered: list[tuple[str, str]] = []
    for message in messages:
        if message.role == "user":
            role = "System" if message.synthetic_kind else "User"
            rendered.append((role, message.content))
        elif message.role == "assistant" and message.tool_calls:
            if message.content:
                rendered.append(("Assistant", message.content))
            for call in message.tool_calls:
                rendered.append(("Tool", _format_tool_call(call)))
        elif message.role == "assistant":
            rendered.append(("Assistant", message.content))
        elif message.role == "tool":
            rendered.append(("Tool", f"工具结果：{message.tool_name or 'unknown'}"))
    return rendered


def _format_memory_result(result: MemoryUpdateResult) -> str:
    if result.updated:
        return f"笔记已更新：{result.reason}。"
    if result.error:
        return f"笔记未更新：{result.error}"
    return f"笔记未更新：{result.reason}"


def _format_tool_call(call: ToolCall) -> str:
    summary = _compact_arguments(call.arguments)
    if summary:
        return f"[bold]调用工具[/bold] [cyan]{escape(call.name)}[/cyan]: {escape(summary)}"
    return f"[bold]调用工具[/bold] [cyan]{escape(call.name)}[/cyan]"


def _compact_arguments(arguments: dict[str, Any]) -> str:
    if not arguments:
        return ""
    text = json.dumps(arguments, ensure_ascii=False)
    if len(text) <= 120:
        return text
    return text[:117] + "..."


def _center_line(line: str) -> str:
    return line.center(WELCOME_WIDTH)
