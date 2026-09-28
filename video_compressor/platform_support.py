"""Small, Qt-independent platform conventions used by the desktop app."""

from __future__ import annotations

import os
import platform
import sys
from collections.abc import Mapping
from pathlib import Path


def runtime_arch() -> str:
    """Return the native runtime architecture with a Windows fallback."""
    return platform.machine().strip() or os.environ.get("PROCESSOR_ARCHITECTURE", "")


def default_log_dir(
    platform_name: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Return the native per-user log directory without creating it."""
    env = os.environ if environ is None else environ
    override = env.get("VIDEO_COMPRESSOR_LOG_DIR")
    if override:
        return Path(override).expanduser()

    target = sys.platform if platform_name is None else platform_name
    user_home = Path.home() if home is None else home
    if target == "darwin":
        return user_home / "Library" / "Logs" / "VideoCompressor"
    if target == "win32":
        return Path(env.get("LOCALAPPDATA", str(user_home))) / "VideoCompressor" / "logs"
    return Path(env.get("XDG_STATE_HOME", str(user_home / ".local" / "state"))) / "video-compressor"


def start_shortcut_text(platform_name: str | None = None) -> str:
    """Return the platform-native shortcut used to start compression."""
    return "Meta+Return" if (platform_name or sys.platform) == "darwin" else "Ctrl+Return"


def remove_shortcut_texts(platform_name: str | None = None) -> tuple[str, ...]:
    """Return keys that remove highlighted queue rows on this platform."""
    if (platform_name or sys.platform) == "darwin":
        return ("Backspace", "Delete")
    return ("Delete",)


def supports_sleep_inhibition(platform_name: str | None = None) -> bool:
    """Return whether the platform has an idle-sleep inhibition backend."""
    return (platform_name or sys.platform) in {"darwin", "win32"}


def supports_process_pause(platform_name: str | None = None) -> bool:
    """Return whether FFmpeg can be paused and resumed in place."""
    return (platform_name or sys.platform) in {"darwin", "win32"}
