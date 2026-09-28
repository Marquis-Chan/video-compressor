"""Platform idle-sleep inhibition for long-running compression jobs."""

from __future__ import annotations

import ctypes
import logging
import subprocess
import sys
import threading
from collections.abc import Callable
from typing import Any, Protocol

from video_compressor.diagnostics import record_event


ES_SYSTEM_REQUIRED = 0x00000001
ES_CONTINUOUS = 0x80000000


class SleepInhibitorProtocol(Protocol):
    @property
    def active(self) -> bool: ...

    def start(self) -> bool: ...

    def stop(self) -> None: ...


def _set_thread_execution_state(flags: int) -> int:
    try:
        setter = ctypes.windll.kernel32.SetThreadExecutionState  # type: ignore[attr-defined]
    except AttributeError:
        return 0
    return int(setter(flags))


class WindowsSleepInhibitor:
    """Keep Windows awake for idle time while allowing the display to turn off."""

    def __init__(
        self,
        enabled: bool = True,
        *,
        platform_name: str | None = None,
        execution_state: Callable[[int], int] = _set_thread_execution_state,
    ) -> None:
        self.enabled = bool(enabled)
        self.platform_name = platform_name or sys.platform
        self._execution_state = execution_state
        self._lock = threading.Lock()
        self._active = False

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active

    def start(self) -> bool:
        if not self.enabled or self.platform_name != "win32":
            return False
        with self._lock:
            if self._active:
                return True
            try:
                result = self._execution_state(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
            except (AttributeError, OSError, ValueError) as exc:
                record_event(logging.WARNING, "sleep_inhibitor_start_failed", error=str(exc))
                return False
            if not result:
                record_event(
                    logging.WARNING,
                    "sleep_inhibitor_start_failed",
                    error="SetThreadExecutionState returned 0",
                )
                return False
            self._active = True
            return True

    def stop(self) -> None:
        with self._lock:
            if not self._active:
                return
            self._active = False
            try:
                result = self._execution_state(ES_CONTINUOUS)
            except (AttributeError, OSError, ValueError) as exc:
                record_event(logging.WARNING, "sleep_inhibitor_stop_failed", error=str(exc))
                return
            if not result:
                record_event(
                    logging.WARNING,
                    "sleep_inhibitor_stop_failed",
                    error="SetThreadExecutionState returned 0",
                )


class MacSleepInhibitor:
    """Own a ``caffeinate -i`` helper without making compression depend on it."""

    def __init__(
        self,
        enabled: bool = True,
        *,
        platform_name: str | None = None,
        popen_factory: Callable[..., Any] = subprocess.Popen,
    ) -> None:
        self.enabled = bool(enabled)
        self.platform_name = platform_name or sys.platform
        self._popen_factory = popen_factory
        self._lock = threading.Lock()
        self._process: Any = None

    @property
    def active(self) -> bool:
        with self._lock:
            process = self._process
            return process is not None and process.poll() is None

    def start(self) -> bool:
        if not self.enabled or self.platform_name != "darwin":
            return False
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return True
            try:
                self._process = self._popen_factory(
                    ["/usr/bin/caffeinate", "-i"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    close_fds=True,
                )
            except OSError as exc:
                self._process = None
                record_event(
                    logging.WARNING,
                    "sleep_inhibitor_start_failed",
                    error=str(exc),
                )
                return False
        return True

    def stop(self) -> None:
        with self._lock:
            process = self._process
            self._process = None
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                record_event(logging.WARNING, "sleep_inhibitor_stop_timeout")
        except OSError as exc:
            record_event(
                logging.WARNING,
                "sleep_inhibitor_stop_failed",
                error=str(exc),
            )


def create_sleep_inhibitor(
    enabled: bool = True,
    platform_name: str | None = None,
) -> SleepInhibitorProtocol:
    target = platform_name or sys.platform
    if target == "win32":
        return WindowsSleepInhibitor(enabled=enabled, platform_name=target)
    return MacSleepInhibitor(enabled=enabled, platform_name=target)
