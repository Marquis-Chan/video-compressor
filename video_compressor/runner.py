"""Thread-safe process lifecycle control for FFmpeg invocations."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any


# Windows does not expose the POSIX stop/continue constants. Keeping portable
# numeric fallbacks lets the macOS branch be unit-tested on Windows; real macOS
# still uses the constants provided by its signal module.
SIGSTOP = getattr(signal, "SIGSTOP", 19)
SIGCONT = getattr(signal, "SIGCONT", 18)


def process_startup_kwargs() -> dict[str, Any]:
    """Return platform-specific Popen settings suitable for background work."""
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {"start_new_session": True}


@dataclass(frozen=True)
class ProcessRunResult:
    returncode: int | None = None
    stdout: bytes | str = b""
    stderr: bytes | str = b""
    cancelled: bool = False
    timed_out: bool = False


def _cancelled(cancel_token: Any) -> bool:
    if cancel_token is None:
        return False
    is_cancelled = getattr(cancel_token, "is_cancelled", None)
    return bool(is_cancelled()) if callable(is_cancelled) else bool(cancel_token())


def close_process_streams(process: Any) -> None:
    if process is None:
        return
    for name in ("stdout", "stderr"):
        stream = getattr(process, name, None)
        close = getattr(stream, "close", None)
        if callable(close):
            try:
                close()
            except OSError:
                pass


class ProcessController:
    """Own one process and allow cancellation from another thread without I/O waits."""

    def __init__(
        self,
        popen_factory: Callable[..., Any] = subprocess.Popen,
        clock: Callable[[], float] = time.monotonic,
        *,
        platform_name: str | None = None,
        psutil_module: Any = None,
    ) -> None:
        self._popen_factory = popen_factory
        self._clock = clock
        self._platform_name = platform_name or sys.platform
        self._psutil = psutil_module
        self._native_process: Any = None
        self._lock = threading.Lock()
        self._cancel_requested = threading.Event()
        self._process: Any = None
        self._terminate_deadline: float | None = None
        self._paused = False

    def start(self, command: Sequence[str], **kwargs: Any) -> Any:
        options = process_startup_kwargs()
        options.update(kwargs)
        process = self._popen_factory(command, **options)
        with self._lock:
            self._process = process
            self._native_process = None
            self._paused = False
        return process

    def _load_psutil_locked(self) -> Any:
        module = self._psutil
        if module is None:
            import psutil as module

            self._psutil = module
        return module

    def _windows_process_locked(self, pid: int) -> Any:
        if self._native_process is None:
            module = self._load_psutil_locked()
            self._native_process = module.Process(pid)
        return self._native_process

    def _signal_process_group_locked(self, process: Any, sent_signal: int) -> bool:
        killpg = getattr(os, "killpg", None)
        if not callable(killpg):
            return False
        try:
            killpg(process.pid, sent_signal)
        except OSError:
            return False
        return True

    def _resume_locked(self, process: Any) -> bool:
        if self._platform_name == "darwin":
            return self._signal_process_group_locked(process, SIGCONT)
        if self._platform_name == "win32":
            try:
                module = self._load_psutil_locked()
            except ImportError:
                return False
            try:
                self._windows_process_locked(process.pid).resume()
            except (module.Error, OSError):
                return False
            return True
        return False

    def request_cancel(self) -> bool:
        """Request process termination without waiting for process pipes or exit."""
        self._cancel_requested.set()
        with self._lock:
            process = self._process
            was_paused = self._paused
            self._paused = False
            if self._terminate_deadline is None:
                self._terminate_deadline = self._clock() + 3.0
            if process is not None and process.poll() is None and was_paused:
                self._resume_locked(process)
        if process is None or process.poll() is not None:
            return False
        process.terminate()
        return True

    def pause(self) -> bool:
        """Pause the owned FFmpeg process when supported by the platform."""
        if self._platform_name not in {"darwin", "win32"}:
            return False
        with self._lock:
            process = self._process
            if process is None or process.poll() is not None:
                self._paused = False
                return False
            if self._paused:
                return True
            if self._platform_name == "darwin":
                if not self._signal_process_group_locked(process, SIGSTOP):
                    return False
            else:
                try:
                    module = self._load_psutil_locked()
                except ImportError:
                    return False
                try:
                    self._windows_process_locked(process.pid).suspend()
                except (module.Error, OSError):
                    return False
            self._paused = True
            return True

    def resume(self) -> bool:
        """Resume a process group previously paused by :meth:`pause`."""
        if self._platform_name not in {"darwin", "win32"}:
            return False
        with self._lock:
            process = self._process
            if process is None or process.poll() is not None:
                self._paused = False
                return False
            if not self._paused:
                return True
            if not self._resume_locked(process):
                return False
            self._paused = False
            return True

    def wait(self, timeout: float | None = None) -> int | None:
        with self._lock:
            process = self._process
        if process is None:
            return None
        if not self._cancel_requested.is_set():
            return process.wait(timeout=timeout)

        with self._lock:
            deadline = self._terminate_deadline
        deadline = deadline if deadline is not None else self._clock() + 3.0
        while process.poll() is None:
            remaining = deadline - self._clock()
            if remaining <= 0:
                process.kill()
                try:
                    return process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    return process.returncode
            try:
                process.wait(timeout=min(0.1, remaining))
            except subprocess.TimeoutExpired:
                continue
        return process.returncode


def run_bounded_process(
    command: Sequence[str],
    *,
    cancel_token: Any = None,
    timeout: float | None = 30,
    poll_interval: float = 0.1,
    controller: ProcessController | None = None,
    **popen_kwargs: Any,
) -> ProcessRunResult:
    if _cancelled(cancel_token):
        return ProcessRunResult(cancelled=True)

    owner = controller or ProcessController()
    process = owner.start(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **popen_kwargs,
    )
    deadline = time.monotonic() + timeout if timeout is not None else None
    stdout: bytes | str = b""
    stderr: bytes | str = b""
    cancelled = False
    timed_out = False
    try:
        while True:
            if _cancelled(cancel_token):
                cancelled = True
                owner.request_cancel()
                break
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                timed_out = True
                owner.request_cancel()
                break
            wait_slice = poll_interval if remaining is None else min(poll_interval, remaining)
            try:
                stdout, stderr = process.communicate(timeout=max(0.001, wait_slice))
                break
            except subprocess.TimeoutExpired:
                continue

        if cancelled or timed_out:
            owner.wait()
            try:
                stdout, stderr = process.communicate(timeout=1)
            except subprocess.TimeoutExpired as exc:
                stdout = exc.output or b""
                stderr = exc.stderr or b""
        return ProcessRunResult(
            returncode=getattr(process, "returncode", None),
            stdout=stdout or b"",
            stderr=stderr or b"",
            cancelled=cancelled,
            timed_out=timed_out,
        )
    finally:
        close_process_streams(process)
