"""Conservative file-level converge merge for AgentTeam workdirs."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from miniclaudecode.teams.core import TeamOperationResult


@dataclass(frozen=True)
class MergeSource:
    member: str
    workdir: Path
    files: tuple[str, ...]


class ConvergeMerger:
    """Apply non-conflicting member file outputs to a target directory."""

    def __init__(self, target: str | Path) -> None:
        self.target = Path(target)

    def merge(self, sources: tuple[MergeSource, ...]) -> TeamOperationResult:
        owners: dict[str, str] = {}
        for source in sources:
            for file in source.files:
                if file in owners and owners[file] != source.member:
                    return TeamOperationResult(False, f"收敛合并冲突：{file} 同时来自 {owners[file]} 和 {source.member}。", metadata={"conflict": file})
                owners[file] = source.member
        applied: list[str] = []
        backups: list[tuple[Path, bytes | None]] = []
        try:
            for source in sources:
                for relative in source.files:
                    src = (source.workdir / relative).resolve()
                    dst = (self.target / relative).resolve()
                    if not str(dst).startswith(str(self.target.resolve())):
                        raise ValueError(f"目标文件越界：{relative}")
                    if not src.exists() or not src.is_file():
                        raise ValueError(f"来源文件不存在：{src}")
                    backups.append((dst, dst.read_bytes() if dst.exists() else None))
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(src, dst)
                    applied.append(relative)
        except Exception as exc:
            for path, content in reversed(backups):
                if content is None:
                    if path.exists():
                        path.unlink()
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(content)
            return TeamOperationResult(False, f"收敛合并失败，已回滚可回滚部分：{exc}", metadata={"applied_before_rollback": applied})
        return TeamOperationResult(True, f"收敛合并完成：{len(applied)} 个文件。", metadata={"files": applied})
