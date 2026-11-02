"""Backend detection helpers for AgentTeam."""

from __future__ import annotations

import platform
import shutil

from miniclaudecode.teams.core import BackendDetection, TeamBackendKind


def detect_in_process() -> BackendDetection:
    return BackendDetection(TeamBackendKind.IN_PROCESS, True, "in_process 后端可用。")


def detect_tmux() -> BackendDetection:
    available = shutil.which("tmux") is not None
    return BackendDetection(
        TeamBackendKind.TMUX_PANE,
        available,
        "tmux 可用。" if available else "未找到 tmux 可执行文件。",
    )


def detect_iterm2() -> BackendDetection:
    if platform.system().lower() != "darwin":
        return BackendDetection(TeamBackendKind.ITERM2_PANE, False, "iTerm2 pane 后端仅在 macOS 上可用。")
    available = shutil.which("osascript") is not None
    return BackendDetection(
        TeamBackendKind.ITERM2_PANE,
        available,
        "iTerm2 自动化入口可用。" if available else "未找到 osascript。",
    )
