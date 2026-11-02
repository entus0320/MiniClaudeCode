"""Context management, tool-result offloading, and conversation compaction."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from miniclaudecode.prompting import AssembledPrompt, DynamicInstruction
from miniclaudecode.session import Message, SlimmedToolResult, ToolResultOffloadInfo
from miniclaudecode.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from miniclaudecode.providers.base import BaseProvider, ProviderTurnComplete
    from miniclaudecode.session import Session
else:
    ProviderTurnComplete = Any

DEFAULT_ARTIFACT_DIR = Path(".miniclaudecode/context")
SUMMARY_HEADINGS = (
    "主要请求",
    "关键概念",
    "文件代码",
    "错误修复",
    "解决过程",
    "用户原话",
    "待办",
    "当前工作",
    "下一步",
)


@dataclass(frozen=True)
class ContextManagementConfig:
    """Runtime configuration for context management."""

    enabled: bool = True
    artifact_dir: Path = DEFAULT_ARTIFACT_DIR
    single_tool_result_token_limit: int = 8_000
    tool_result_batch_token_limit: int = 24_000
    tool_result_preview_chars: int = 2_000
    compact_trigger_ratio: float = 0.8
    compact_target_ratio: float = 0.45
    context_window_tokens: int | None = None
    fallback_context_window_tokens: int = 128_000
    recent_message_groups_to_keep: int = 6
    max_auto_compact_failures: int = 3


@dataclass(frozen=True)
class ContextArtifact:
    """A local artifact containing content omitted from model history."""

    path: Path
    relative_path: str
    bytes: int
    sha256: str
    kind: str


@dataclass
class CompactionState:
    """Mutable compaction state for one running process."""

    auto_enabled: bool = True
    consecutive_failures: int = 0
    compact_count: int = 0
    last_error: str | None = None


@dataclass(frozen=True)
class ContextManagementResult:
    """Observable result for one context-management action."""

    compacted: bool = False
    reason: str = "not_needed"
    input_tokens: int | None = None
    context_window_tokens: int | None = None
    summary_message_count: int = 0
    retained_message_count: int = 0
    before_estimated_tokens: int | None = None
    after_estimated_tokens: int | None = None
    released_estimated_tokens: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class MessageGroup:
    """A group of messages that must be kept or compacted together."""

    messages: tuple[Message, ...]
    compactable: bool = True


@dataclass(frozen=True)
class _SlimCandidate:
    result: ToolResult
    model_text: str
    estimated_tokens: int
    offload_reason: str | None = None


class ContextArtifactStore:
    """Write full context artifacts under the project workdir."""

    def __init__(self, root: str | Path, *, relative_root: str | Path | None = None) -> None:
        self.root = Path(root)
        self.relative_root = Path(relative_root) if relative_root is not None else None
        self.tool_results_dir = self.root / "tool-results"
        self._counter = 0

    def write_tool_result(self, result: ToolResult, model_text: str) -> ContextArtifact:
        """Persist one full tool result and return its artifact metadata."""

        self.tool_results_dir.mkdir(parents=True, exist_ok=True)
        self._counter += 1
        digest = hashlib.sha256(model_text.encode("utf-8")).hexdigest()
        safe_tool = _safe_filename_part(result.tool_name)
        safe_call = _safe_filename_part(result.tool_call_id or "no-call-id")
        filename = f"{self._counter:06d}-{safe_tool}-{safe_call}-{digest[:12]}.json"
        path = self.tool_results_dir / filename
        payload = {
            "kind": "tool_result",
            "tool_name": result.tool_name,
            "tool_call_id": result.tool_call_id,
            "success": result.success,
            "content": result.content,
            "error": result.error,
            "metadata": result.metadata,
            "model_text": model_text,
            "sha256": digest,
        }
        data = json.dumps(payload, ensure_ascii=False, indent=2)
        path.write_text(data, encoding="utf-8")
        relative_path = (
            (self.relative_root / "tool-results" / filename).as_posix()
            if self.relative_root is not None
            else path.as_posix()
        )
        return ContextArtifact(
            path=path,
            relative_path=relative_path,
            bytes=len(data.encode("utf-8")),
            sha256=digest,
            kind="tool_result",
        )


class ToolResultSlimmer:
    """Replace oversized tool results with previews and artifact references."""

    def __init__(self, config: ContextManagementConfig, store: ContextArtifactStore) -> None:
        self.config = config
        self.store = store

    def slim_result(self, result: ToolResult) -> str:
        """Return the model text for one tool result after optional offloading."""

        return self.slim_results([result])[0]

    def slim_results(self, results: Sequence[ToolResult]) -> list[str]:
        """Return model texts for a batch of tool results, preserving order."""

        return [item.content for item in self.slim_results_detailed(results)]

    def slim_results_detailed(self, results: Sequence[ToolResult]) -> list[SlimmedToolResult]:
        """Return model texts with offload metadata for a batch of tool results."""

        candidates = [
            _SlimCandidate(
                result=result,
                model_text=result.to_model_text(),
                estimated_tokens=estimate_tokens_from_text(result.to_model_text()),
            )
            for result in results
        ]
        if not self.config.enabled:
            return [SlimmedToolResult(content=candidate.model_text) for candidate in candidates]

        mutable = list(candidates)
        for index, candidate in enumerate(mutable):
            if candidate.estimated_tokens > self.config.single_tool_result_token_limit:
                mutable[index] = _SlimCandidate(
                    result=candidate.result,
                    model_text=candidate.model_text,
                    estimated_tokens=candidate.estimated_tokens,
                    offload_reason="single_tool_result_limit",
                )

        total = sum(
            candidate.estimated_tokens
            for candidate in mutable
            if candidate.offload_reason is None
        )
        if total > self.config.tool_result_batch_token_limit:
            for index in sorted(
                range(len(mutable)),
                key=lambda item: mutable[item].estimated_tokens,
                reverse=True,
            ):
                candidate = mutable[index]
                if candidate.offload_reason is not None:
                    continue
                mutable[index] = _SlimCandidate(
                    result=candidate.result,
                    model_text=candidate.model_text,
                    estimated_tokens=candidate.estimated_tokens,
                    offload_reason="tool_result_batch_limit",
                )
                total -= candidate.estimated_tokens
                if total <= self.config.tool_result_batch_token_limit:
                    break

        output: list[SlimmedToolResult] = []
        for candidate in mutable:
            if candidate.offload_reason is None:
                output.append(SlimmedToolResult(content=candidate.model_text))
            else:
                output.append(self._offload(candidate))
        return output

    def _offload(self, candidate: _SlimCandidate) -> SlimmedToolResult:
        artifact = self.store.write_tool_result(candidate.result, candidate.model_text)
        content_preview = _preview(candidate.result.content or candidate.result.error or "", self.config.tool_result_preview_chars)
        error_preview = _preview(candidate.result.error or "", self.config.tool_result_preview_chars) if candidate.result.error else None
        payload = {
            "tool": candidate.result.tool_name,
            "success": candidate.result.success,
            "content_preview": content_preview,
            "error_preview": error_preview,
            "offloaded": {
                "path": artifact.relative_path,
                "bytes": artifact.bytes,
                "sha256": artifact.sha256,
                "reason": candidate.offload_reason,
                "original_estimated_tokens": candidate.estimated_tokens,
            },
            "metadata": _preview_metadata(candidate.result.metadata, self.config.tool_result_preview_chars),
        }
        content = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        slimmed_tokens = estimate_tokens_from_text(content)
        offload = ToolResultOffloadInfo(
            tool_name=candidate.result.tool_name,
            tool_call_id=candidate.result.tool_call_id,
            path=artifact.relative_path,
            bytes=artifact.bytes,
            sha256=artifact.sha256,
            reason=candidate.offload_reason or "unknown",
            original_chars=len(candidate.model_text),
            slimmed_chars=len(content),
            released_chars=max(0, len(candidate.model_text) - len(content)),
            original_estimated_tokens=candidate.estimated_tokens,
            slimmed_estimated_tokens=slimmed_tokens,
            released_estimated_tokens=max(0, candidate.estimated_tokens - slimmed_tokens),
            content_preview=content_preview,
            error_preview=error_preview,
        )
        return SlimmedToolResult(content=content, offload=offload)


class ConversationCompactor:
    """Generate structured summaries and replace old session history safely."""

    def __init__(self, config: ContextManagementConfig, state: CompactionState | None = None) -> None:
        self.config = config
        self.state = state or CompactionState()

    def maybe_compact(
        self,
        *,
        session: Session,
        provider: BaseProvider,
        tools: Sequence[Tool] | None,
        prompt: AssembledPrompt | None,
        allow_auto_compact: bool = True,
    ) -> ContextManagementResult:
        """Compact automatically when token budget says it is necessary."""

        if not self.config.enabled:
            return ContextManagementResult(reason="disabled")
        if not allow_auto_compact:
            return ContextManagementResult(reason="auto_compact_not_allowed")
        if not self.state.auto_enabled:
            return ContextManagementResult(reason="auto_compact_fused", error=self.state.last_error)

        history = session.get_history()
        window = (
            self.config.context_window_tokens
            or provider.context_window_tokens
            or self.config.fallback_context_window_tokens
        )

        input_tokens = provider.count_tokens(history, tools=tools, prompt=prompt)
        if input_tokens is None:
            input_tokens = estimate_messages_tokens(history)
        if input_tokens < math.floor(window * self.config.compact_trigger_ratio):
            return ContextManagementResult(reason="under_budget", input_tokens=input_tokens, context_window_tokens=window)

        result = self.compact_now(session=session, provider=provider, reason="auto")
        return ContextManagementResult(
            compacted=result.compacted,
            reason=result.reason,
            input_tokens=input_tokens,
            context_window_tokens=window,
            summary_message_count=result.summary_message_count,
            retained_message_count=result.retained_message_count,
            before_estimated_tokens=result.before_estimated_tokens,
            after_estimated_tokens=result.after_estimated_tokens,
            released_estimated_tokens=result.released_estimated_tokens,
            error=result.error,
        )

    def compact_now(
        self,
        *,
        session: Session,
        provider: BaseProvider,
        reason: str = "manual",
        recent_message_groups_to_keep: int | None = None,
        force: bool = False,
    ) -> ContextManagementResult:
        """Force one compaction attempt."""

        if not self.config.enabled:
            return ContextManagementResult(reason="disabled")
        history = session.get_history()
        before_estimated_tokens = estimate_messages_tokens(history)
        groups = group_messages(history)
        keep_groups = (
            self.config.recent_message_groups_to_keep
            if recent_message_groups_to_keep is None
            else recent_message_groups_to_keep
        )
        if force:
            if len(groups) <= 1:
                return ContextManagementResult(reason="not_enough_history")
        elif len(groups) <= keep_groups:
            return ContextManagementResult(reason="not_enough_history")

        safe_cut = _safe_compaction_cut(groups, keep_groups)
        if safe_cut <= 0:
            return ContextManagementResult(reason="no_safe_compaction_boundary")
        compact_groups = groups[:safe_cut]
        retained_groups = groups[safe_cut:]
        if not compact_groups:
            return ContextManagementResult(reason="not_enough_history")

        compact_messages = [message for group in compact_groups for message in group.messages]
        retained_messages = [message for group in retained_groups for message in group.messages]
        try:
            summary = self._summarize(provider, compact_messages)
        except Exception as exc:
            return self._record_failure(str(exc))

        summary_message = Message(
            role="user",
            content=f"<context-summary>\n{summary}\n</context-summary>",
            synthetic_kind="context_summary",
        )
        boundary_message = Message(
            role="user",
            content=(
                "<context-boundary>\n"
                "以上结构化摘要替代了更早的完整对话历史。若需要文件内容、代码片段、命令输出或工具结果细节，"
                "必须重新读取原始文件或 artifact；不要根据摘要脑补精确内容。\n"
                "</context-boundary>"
            ),
            synthetic_kind="context_boundary",
        )
        session.replace_history([summary_message, boundary_message, *retained_messages])
        after_estimated_tokens = estimate_messages_tokens(session.get_history())
        self.state.compact_count += 1
        self.state.consecutive_failures = 0
        self.state.auto_enabled = True
        self.state.last_error = None
        return ContextManagementResult(
            compacted=True,
            reason=reason,
            summary_message_count=2,
            retained_message_count=len(retained_messages),
            before_estimated_tokens=before_estimated_tokens,
            after_estimated_tokens=after_estimated_tokens,
            released_estimated_tokens=max(0, before_estimated_tokens - after_estimated_tokens),
        )

    def _summarize(self, provider: BaseProvider, messages: Sequence[Message]) -> str:
        errors: list[str] = []
        for instruction in (summary_instruction(), summary_retry_instruction()):
            try:
                return self._summarize_once(provider, messages, instruction)
            except ValueError as exc:
                errors.append(str(exc))
                if "工具调用" in str(exc):
                    raise
        detail = "；".join(errors) if errors else "未知错误"
        raise ValueError(f"模型未返回可用的结构化摘要：{detail}")

    def _summarize_once(
        self,
        provider: BaseProvider,
        messages: Sequence[Message],
        instruction: DynamicInstruction,
    ) -> str:
        request_message = Message(role="user", content=_build_summary_request(instruction, messages))
        last_complete: ProviderTurnComplete | None = None
        text_parts: list[str] = []
        for event in provider.stream_turn([request_message], tools=[]):
            if hasattr(event, "text") and not hasattr(event, "tool_calls"):
                text_parts.append(event.text)
            elif hasattr(event, "tool_calls") and hasattr(event, "text"):
                last_complete = event
        if last_complete is None:
            raise ValueError("摘要调用没有返回结果。")
        if last_complete.tool_calls:
            raise ValueError("摘要调用返回了工具调用，已拒绝执行。")
        raw_text = (last_complete.text or "".join(text_parts)).strip()
        if not raw_text:
            raise ValueError("摘要调用返回空文本。")
        formal = extract_formal_summary(raw_text)
        validate_summary_headings(formal)
        if getattr(last_complete, "stop_reason", None) in {"max_tokens", "length"}:
            raise ValueError("摘要输出被截断。")
        return formal

    def _record_failure(self, error: str) -> ContextManagementResult:
        self.state.consecutive_failures += 1
        self.state.last_error = error
        if self.state.consecutive_failures >= self.config.max_auto_compact_failures:
            self.state.auto_enabled = False
        return ContextManagementResult(reason="summary_failed", error=error)


class ContextManager:
    """Facade used by Session, AgentRunner, and TUI."""

    def __init__(self, config: ContextManagementConfig, *, workdir: str | Path) -> None:
        self.config = config
        artifact_root = Path(workdir) / config.artifact_dir
        self.artifact_store = ContextArtifactStore(artifact_root, relative_root=config.artifact_dir)
        self.tool_result_slimmer = ToolResultSlimmer(config, self.artifact_store)
        self.compactor = ConversationCompactor(config)

    @property
    def state(self) -> CompactionState:
        return self.compactor.state

    def prepare_before_request(
        self,
        *,
        session: Session,
        provider: BaseProvider,
        tools: Sequence[Tool] | None,
        prompt: AssembledPrompt | None,
        allow_auto_compact: bool = True,
    ) -> ContextManagementResult:
        """Run request-time context management before a provider call."""

        return self.compactor.maybe_compact(
            session=session,
            provider=provider,
            tools=tools,
            prompt=prompt,
            allow_auto_compact=allow_auto_compact,
        )

    def manual_compact(self, *, session: Session, provider: BaseProvider) -> ContextManagementResult:
        """Force a user-requested compaction attempt."""

        return self.compactor.compact_now(
            session=session,
            provider=provider,
            reason="manual",
            recent_message_groups_to_keep=1,
            force=True,
        )


def _safe_compaction_cut(groups: Sequence[MessageGroup], recent_groups_to_keep: int) -> int:
    desired_cut = max(0, len(groups) - recent_groups_to_keep)
    for cut in range(desired_cut, 0, -1):
        if all(group.compactable for group in groups[:cut]):
            return cut
    return 0



def group_messages(messages: Sequence[Message]) -> list[MessageGroup]:
    """Group messages without splitting assistant tool calls from their results."""

    groups: list[MessageGroup] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        if message.role == "assistant" and message.tool_calls:
            expected = {call.id for call in message.tool_calls}
            grouped = [message]
            index += 1
            while index < len(messages) and messages[index].role == "tool" and messages[index].tool_call_id in expected:
                grouped.append(messages[index])
                expected.discard(messages[index].tool_call_id or "")
                index += 1
            groups.append(MessageGroup(messages=tuple(grouped), compactable=not expected))
            continue
        groups.append(MessageGroup(messages=(message,)))
        index += 1
    return groups



def estimate_messages_tokens(messages: Sequence[Message]) -> int:
    return sum(estimate_tokens_from_text(message.content) for message in messages)


def estimate_tokens_from_text(text: str) -> int:
    """Conservative provider-neutral token estimate for mixed Chinese/code/JSON text."""

    return max(1, math.ceil(len(text) / 3))


def summary_instruction() -> DynamicInstruction:
    return DynamicInstruction(
        name="context_compaction",
        content=_summary_instruction_text(retry=False),
    )


def summary_retry_instruction() -> DynamicInstruction:
    return DynamicInstruction(
        name="context_compaction_retry",
        content=_summary_instruction_text(retry=True),
    )


def _summary_instruction_text(*, retry: bool) -> str:
    prefix = "上一次摘要格式不合格，请重新输出。\n\n" if retry else ""
    return (
        "[上下文压缩任务开始：工具不可用，禁止工具调用]\n\n"
        f"{prefix}"
        "你正在为 MiniClaudeCode 压缩旧对话历史。不要调用工具，不要请求工具，不要描述将要调用工具。\n"
        "你只能根据输入的历史消息生成摘要。不要编造未出现的文件内容、命令输出或工具结果。\n\n"
        "只输出一个 XML 块，不要输出草稿、解释、前言或代码围栏。\n"
        "第一行必须是 <formal_summary>，最后一行必须是 </formal_summary>。\n"
        "每个固定栏目都必须保留；没有信息时写“无”或“不确定”。\n\n"
        "<formal_summary>\n"
        "## 主要请求\n...\n"
        "## 关键概念\n...\n"
        "## 文件代码\n...\n"
        "## 错误修复\n...\n"
        "## 解决过程\n...\n"
        "## 用户原话\n...\n"
        "## 待办\n...\n"
        "## 当前工作\n...\n"
        "## 下一步\n...\n"
        "</formal_summary>\n\n"
        "要求：用户原话尽量逐字保留重要用户请求；文件、代码、错误、工具结果只能写已观察到的事实；"
        "不确定的信息标注“不确定”；不要根据摘要脑补文件细节。\n"
        "再次强调：工具不可用，禁止工具调用。只输出上述一个 XML 块。\n"
        "[上下文压缩任务结束：禁止工具调用]"
    )


def _build_summary_request(instruction: DynamicInstruction, messages: Sequence[Message]) -> str:
    """Render the to-compact history and the summary instruction as one user turn.

    The instruction must arrive as the model's current task, not as a system
    reminder appended to an already-finished conversation. Otherwise the model
    tends to continue the old dialogue instead of emitting a formal_summary.
    """

    transcript = _render_transcript(messages)
    return (
        f"{instruction.content.strip()}\n\n"
        "====== 待压缩对话历史开始 ======\n"
        f"{transcript}\n"
        "====== 待压缩对话历史结束 ======\n\n"
        "现在请只输出 <formal_summary> XML 块，对以上历史进行结构化总结。"
    )


def _render_transcript(messages: Sequence[Message]) -> str:
    lines: list[str] = []
    for message in messages:
        if message.role == "assistant" and message.tool_calls:
            calls = "、".join(call.name for call in message.tool_calls)
            text = message.content.strip()
            if text:
                lines.append(f"[assistant] {text}")
            lines.append(f"[assistant 工具调用] {calls}")
        elif message.role == "tool":
            name = message.tool_name or "unknown"
            if message.tool_result_offload is not None:
                offload = message.tool_result_offload
                detail = f"（大结果已落盘：{offload.path}）{offload.content_preview}"
            else:
                detail = message.content
            lines.append(f"[工具结果 {name}] {detail}".rstrip())
        else:
            lines.append(f"[{message.role}] {message.content}".rstrip())
    return "\n".join(lines).strip() or "（无历史内容）"


def extract_formal_summary(text: str) -> str:
    cleaned = _strip_code_fence(text.strip())
    match = re.search(r"<formal_summary[^>]*>\s*(.*?)\s*</formal_summary>", cleaned, flags=re.DOTALL | re.IGNORECASE)
    if match is None:
        raise ValueError("摘要结果缺少 formal_summary。")
    formal = match.group(1).strip()
    if not formal:
        raise ValueError("formal_summary 为空。")
    return _normalize_summary_headings(formal)


def validate_summary_headings(summary: str) -> None:
    missing = [heading for heading in SUMMARY_HEADINGS if not _has_summary_heading(summary, heading)]
    if missing:
        raise ValueError(f"摘要缺少固定栏目：{', '.join(missing)}")


def _strip_code_fence(text: str) -> str:
    match = re.fullmatch(r"```(?:\w+)?\s*(.*?)\s*```", text, flags=re.DOTALL)
    return match.group(1).strip() if match is not None else text



def _has_summary_heading(text: str, heading: str) -> bool:
    return re.search(rf"^\s*#+\s*{re.escape(heading)}\s*$", text, flags=re.MULTILINE) is not None


def _normalize_summary_headings(text: str) -> str:
    normalized = text
    for heading in SUMMARY_HEADINGS:
        normalized = re.sub(
            rf"^\s*#+\s*{re.escape(heading)}\s*$",
            f"## {heading}",
            normalized,
            flags=re.MULTILINE,
        )
    return normalized.strip()


def _safe_filename_part(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-._")
    return safe[:64] or "item"


def _preview_metadata(metadata: dict[str, Any], limit: int) -> dict[str, Any]:
    preview: dict[str, Any] = {}
    omitted = False
    for key, value in metadata.items():
        if isinstance(value, str):
            target_key = f"{key}_preview" if key in {"stdout", "stderr"} else key
            if len(value) > limit:
                preview[target_key] = _preview(value, limit)
                omitted = True
            else:
                preview[target_key] = value
            continue
        if value is None or isinstance(value, (bool, int, float)):
            preview[key] = value
            continue

        try:
            serialized = json.dumps(value, ensure_ascii=False)
            serializable_value = value
        except (TypeError, ValueError):
            serialized = json.dumps(str(value), ensure_ascii=False)
            serializable_value = str(value)
        if len(serialized) > limit:
            preview[f"{key}_preview"] = _preview(serialized, limit)
            omitted = True
        else:
            preview[key] = serializable_value
    if omitted:
        preview["metadata_offloaded"] = True
        preview["metadata_note"] = "完整 metadata 已写入本地 artifact。"
    return preview


def _preview(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n[完整工具结果已写入本地 artifact。]"
