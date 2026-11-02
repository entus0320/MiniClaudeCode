"""Provider-neutral prompt assembly for MiniClaudeCode."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class PromptStability(str, Enum):
    """Whether a prompt block is stable enough to be cached."""

    STABLE = "stable"
    DYNAMIC = "dynamic"


@dataclass(frozen=True)
class PromptCachePolicy:
    """Cache intent for one prompt block."""

    cacheable: bool = False

    @classmethod
    def ephemeral(cls) -> "PromptCachePolicy":
        """Mark a stable block as eligible for provider prompt caching."""

        return cls(cacheable=True)

    @classmethod
    def none(cls) -> "PromptCachePolicy":
        """Mark a block as not cacheable."""

        return cls(cacheable=False)


@dataclass(frozen=True)
class PromptModule:
    """A named system instruction module before provider-specific rendering."""

    name: str
    priority: int
    stability: PromptStability
    content: str
    cache_policy: PromptCachePolicy | None = None

    def to_block(self) -> "PromptBlock":
        """Convert this module to an assembled prompt block."""

        return PromptBlock(
            name=self.name,
            text=self.content.strip(),
            stability=self.stability,
            cache_policy=self.cache_policy or _default_cache_policy(self.stability),
        )


@dataclass(frozen=True)
class PromptBlock:
    """A provider-neutral prompt block ready for adapter conversion."""

    name: str
    text: str
    stability: PromptStability
    cache_policy: PromptCachePolicy

    def render(self) -> str:
        """Render with a visible block name for provider fallbacks and tests."""

        return f"[{self.name}]\n{self.text}" if self.text else f"[{self.name}]"


@dataclass(frozen=True)
class RuntimeContext:
    """Dynamic environment context for the current provider request."""

    workdir: Path
    os_name: str
    current_date: str
    provider_name: str
    model: str
    git_status: str | None = None

    def render(self) -> str:
        """Render dynamic environment facts for the model."""

        lines = [
            f"Working directory: {self.workdir}",
            f"Operating system: {self.os_name}",
            f"Current date: {self.current_date}",
            f"Provider: {self.provider_name}",
            f"Model: {self.model}",
        ]
        if self.git_status is not None:
            lines.append(f"Git status: {self.git_status}")
        return "\n".join(lines)


@dataclass(frozen=True)
class DynamicInstruction:
    """A runtime system supplement that is not user conversation history."""

    name: str
    content: str
    priority: int = 1000
    tag: str = "system-reminder"

    def render(self) -> str:
        """Render as a tagged system supplement."""

        text = self.content.strip()
        return f"<{self.tag}>\n{text}\n</{self.tag}>"

    def to_block(self) -> PromptBlock:
        """Convert this instruction to a dynamic prompt block."""

        return PromptBlock(
            name=self.name,
            text=self.render(),
            stability=PromptStability.DYNAMIC,
            cache_policy=PromptCachePolicy.none(),
        )


@dataclass(frozen=True)
class AssembledPrompt:
    """Stable and dynamic prompt blocks for one provider request."""

    stable_blocks: tuple[PromptBlock, ...] = ()
    dynamic_blocks: tuple[PromptBlock, ...] = ()

    @property
    def blocks(self) -> tuple[PromptBlock, ...]:
        """Return blocks in provider rendering order."""

        return (*self.stable_blocks, *self.dynamic_blocks)

    def render_text(self) -> str:
        """Render all prompt blocks as provider-agnostic text."""

        return "\n\n".join(block.render() for block in self.blocks if block.text)


class PromptAssembler:
    """Assemble stable modules, runtime context, and dynamic instructions."""

    def __init__(self, modules: Sequence[PromptModule]) -> None:
        self.modules = tuple(modules)

    def assemble(
        self,
        *,
        runtime_context: RuntimeContext | None = None,
        dynamic_instructions: Iterable[DynamicInstruction] = (),
    ) -> AssembledPrompt:
        """Build stable and dynamic prompt blocks in deterministic order."""

        stable_blocks: list[PromptBlock] = []
        dynamic_blocks: list[PromptBlock] = []
        for module in sorted(self.modules, key=lambda item: (item.priority, item.name)):
            block = module.to_block()
            if block.stability == PromptStability.STABLE:
                stable_blocks.append(block)
            else:
                dynamic_blocks.append(block)

        if runtime_context is not None:
            dynamic_blocks.append(
                PromptBlock(
                    name="runtime_context",
                    text=runtime_context.render(),
                    stability=PromptStability.DYNAMIC,
                    cache_policy=PromptCachePolicy.none(),
                )
            )

        for instruction in sorted(dynamic_instructions, key=lambda item: (item.priority, item.name)):
            dynamic_blocks.append(instruction.to_block())

        return AssembledPrompt(stable_blocks=tuple(stable_blocks), dynamic_blocks=tuple(dynamic_blocks))


def plan_only_instruction() -> DynamicInstruction:
    """Return the standard plan-only runtime reminder."""

    return DynamicInstruction(
        name="plan_only",
        content=(
            "当前处于 plan-only 模式。只能读取和分析信息，不要调用 write_file、edit_file 或 run_command。"
            "如果需要修改文件或执行命令，请输出计划并等待用户去掉 plan-only 限制。"
        ),
        priority=100,
    )


def _default_cache_policy(stability: PromptStability) -> PromptCachePolicy:
    if stability == PromptStability.STABLE:
        return PromptCachePolicy.ephemeral()
    return PromptCachePolicy.none()
