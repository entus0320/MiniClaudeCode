"""Automatic note management for MiniClaudeCode."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from miniclaudecode.providers.base import BaseProvider, ProviderTurnComplete
from miniclaudecode.prompting import PromptModule, PromptStability
from miniclaudecode.session import Message, Session

USER_PREFERENCES = "user-preferences.md"
FEEDBACK = "feedback.md"
PROJECT_KNOWLEDGE = "project-knowledge.md"
REFERENCES = "references.md"
MEMORY_INDEX = "MEMORY.md"

_NOTE_LABELS: dict[str, str] = {
    USER_PREFERENCES: "用户偏好",
    FEEDBACK: "纠正反馈",
    PROJECT_KNOWLEDGE: "项目知识",
    REFERENCES: "参考资料",
}


@dataclass(frozen=True)
class MemoryUpdateResult:
    """Observable result of a memory update attempt."""

    updated: bool
    reason: str
    error: str | None = None


class MemoryManager:
    """Update user-level and project-level notes from recent conversation."""

    def __init__(
        self,
        *,
        user_dir: str | Path,
        project_dir: str | Path,
        auto_every_turns: int = 10,
        recent_messages: int = 20,
        enabled: bool = True,
    ) -> None:
        self.user_dir = Path(user_dir).expanduser()
        self.project_dir = Path(project_dir)
        self.auto_every_turns = auto_every_turns
        self.recent_messages = recent_messages
        self.enabled = enabled
        self._user_turns_since_update = 0

    def note_paths(self) -> dict[str, Path]:
        """Return the fixed note file paths."""

        return {
            "user_preferences": self.user_dir / USER_PREFERENCES,
            "feedback": self.user_dir / FEEDBACK,
            "project_knowledge": self.project_dir / PROJECT_KNOWLEDGE,
            "references": self.project_dir / REFERENCES,
        }

    def list_notes(self) -> list[Path]:
        """Return existing note paths."""

        return [path for path in self.note_paths().values() if path.exists()]

    def load_all_content(self) -> str:
        """Read all existing memory files and return combined text for prompt injection."""

        if not self.enabled:
            return ""
        parts: list[str] = []
        for name, path in self.note_paths().items():
            label = _NOTE_LABELS.get(_filename(path), name)
            text = _read_text(path)
            if text.strip():
                parts.append(f"## {label}（{path}）\n\n{text.strip()}")
        if not parts:
            return ""
        return (
            "<system-reminder>\n"
            "以下内容来自 MiniClaudeCode 的自动笔记系统，是跨会话保留的长期记忆，"
            "由 LLM 自动生成，可能包含项目知识、参考资料、用户偏好和纠正反馈。\n"
            + "\n\n".join(parts)
            + "\n</system-reminder>"
        )

    def to_prompt_module(self) -> PromptModule | None:
        """Convert loaded memories to a dynamic prompt module if any content exists."""

        content = self.load_all_content()
        if not content.strip():
            return None
        return PromptModule(
            name="long_term_memory",
            priority=85,
            stability=PromptStability.DYNAMIC,
            content=content,
        )

    def _write_index(self) -> None:
        """Regenerate MEMORY.md index files for both user and project directories."""

        self._write_one_index(self.user_dir, [USER_PREFERENCES, FEEDBACK])
        self._write_one_index(self.project_dir, [PROJECT_KNOWLEDGE, REFERENCES])

    def _write_one_index(self, directory: Path, filenames: list[str]) -> None:
        lines: list[str] = []
        for filename in filenames:
            path = directory / filename
            if not path.exists():
                continue
            label = _NOTE_LABELS.get(filename, filename)
            summary = _first_line_summary(path)
            lines.append(f"- [{label}]({filename}) — {summary}")
        if lines:
            _atomic_write(directory / MEMORY_INDEX, "\n".join(lines) + "\n")

    def record_user_turn(self, provider: BaseProvider, session: Session) -> MemoryUpdateResult | None:
        """Trigger automatic update every configured number of user turns."""

        if not self.enabled:
            return None
        self._user_turns_since_update += 1
        if self._user_turns_since_update < self.auto_every_turns:
            return None
        self._user_turns_since_update = 0
        return self.update_now(provider, session, reason="auto_every_turns")

    def update_now(self, provider: BaseProvider, session: Session, *, reason: str = "manual") -> MemoryUpdateResult:
        """Ask the model to rewrite notes as strict JSON and persist them atomically."""

        if not self.enabled:
            return MemoryUpdateResult(updated=False, reason="disabled")
        request = Message(role="user", content=self._build_update_prompt(session.get_history()))
        try:
            complete: ProviderTurnComplete | None = None
            text_parts: list[str] = []
            for event in provider.stream_turn([request], tools=[]):
                if hasattr(event, "text") and not hasattr(event, "tool_calls"):
                    text_parts.append(event.text)
                elif isinstance(event, ProviderTurnComplete):
                    complete = event
            raw_text = (complete.text if complete is not None else "".join(text_parts)).strip()
            payload = json.loads(raw_text)
            if not isinstance(payload, dict):
                return MemoryUpdateResult(updated=False, reason=reason, error="模型输出不是 JSON object。")
            self._write_notes(payload)
        except Exception as exc:
            return MemoryUpdateResult(updated=False, reason=reason, error=str(exc))
        return MemoryUpdateResult(updated=True, reason=reason)

    def _build_update_prompt(self, history: list[Message]) -> str:
        paths = self.note_paths()
        current = {name: _read_text(path) for name, path in paths.items()}
        recent = history[-self.recent_messages :]
        transcript = "\n".join(f"[{message.role}] {message.content}" for message in recent)
        return (
            "你正在更新 MiniClaudeCode 的长期笔记。不要调用工具。\n"
            "根据当前笔记和最近对话，输出严格 JSON object，字段必须为："
            "user_preferences_md, feedback_md, project_knowledge_md, references_md。\n"
            "不要输出 Markdown 围栏、解释、前言或额外字段。\n\n"
            "更新规则：\n"
            "1. 保留当前笔记中仍然成立的内容，不要无故清空或改写。\n"
            "2. 如果最近对话出现用户自述偏好、兴趣、习惯、身份、长期目标或稳定个人事实，"
            "必须合并进 user_preferences_md。例如：用户说“我喜欢打篮球”，必须在 user_preferences_md 中记录“用户喜欢打篮球”。\n"
            "3. 如果最近对话出现用户对你的纠正、工作方式要求、输出风格反馈或以后要遵守的做法，"
            "必须合并进 feedback_md。\n"
            "4. 如果最近对话出现项目架构、文件职责、配置约定、实现状态或技术决策，"
            "必须合并进 project_knowledge_md。\n"
            "5. 如果最近对话出现 URL、文档、外部资料、重要文件路径或会话记录引用，"
            "必须合并进 references_md。\n"
            "6. 由你判断去重，程序不会做相似度算法；但不要因为信息看起来简单就忽略明确的新事实。\n\n"
            f"当前 user_preferences_md:\n{current['user_preferences']}\n\n"
            f"当前 feedback_md:\n{current['feedback']}\n\n"
            f"当前 project_knowledge_md:\n{current['project_knowledge']}\n\n"
            f"当前 references_md:\n{current['references']}\n\n"
            f"最近对话:\n{transcript}\n"
        )

    def _write_notes(self, payload: dict[str, object]) -> None:
        paths = self.note_paths()
        mapping = {
            "user_preferences_md": paths["user_preferences"],
            "feedback_md": paths["feedback"],
            "project_knowledge_md": paths["project_knowledge"],
            "references_md": paths["references"],
        }
        for key, path in mapping.items():
            value = payload.get(key)
            if not isinstance(value, str):
                raise ValueError(f"模型输出缺少字符串字段 {key}。")
            _atomic_write(path, value)
        self._write_index()


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _filename(path: Path) -> str:
    return path.name


def _first_line_summary(path: Path) -> str:
    text = _read_text(path)
    if not text:
        return "空"
    lines = text.strip().splitlines()
    for line in lines:
        stripped = line.lstrip("#").strip()
        if stripped:
            if len(stripped) <= 60:
                return stripped
            return stripped[:57] + "..."
    return "无内容"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
