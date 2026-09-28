# -*- coding: utf-8 -*-
"""
Shared compression core for the video compressor CLI and GUI.

The functions in this module avoid direct user interaction so they can be
called from a terminal, a Qt worker thread, or tests.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from video_compressor.jobs import build_ffmpeg_command as _build_v15_ffmpeg_command
from video_compressor.diagnostics import record_event
from video_compressor.models import (
    CompressionProfile,
    CompressionResult,
    JobState,
    MediaInfo,
    ToolchainReport,
)
from video_compressor.probe import probe_media
from video_compressor.profiles import PROFILE_QUALITY, PROFILE_SHARE, PROFILE_SMALL, PROFILES, get_profile
from video_compressor.runner import ProcessController, close_process_streams
from video_compressor.toolchain import validate_toolchain
from video_compressor.verify import verify_output


APP_NAME = "视频压缩工具箱"
APP_VERSION = "1.5.0"

DEFAULT_CRF = 25
DEFAULT_PRESET = "fast"
DEFAULT_AUDIO_BITRATE = "128k"
OUTPUT_SUBFOLDER = "compressed"

VIDEO_EXTENSIONS = (".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm")
PRESETS = (
    "ultrafast",
    "superfast",
    "veryfast",
    "faster",
    "fast",
    "medium",
    "slow",
    "slower",
    "veryslow",
)
AUDIO_BITRATES = ("64k", "96k", "128k", "160k", "192k", "256k")
CONFLICT_POLICIES = ("skip", "overwrite", "rename")
OUTPUT_MODES = ("source_subfolder", "custom")
_PROFILE_UNSET = object()


@dataclass(init=False)
class CompressionOptions:
    crf: int = DEFAULT_CRF
    preset: str = DEFAULT_PRESET
    audio_bitrate: str = DEFAULT_AUDIO_BITRATE
    output_mode: str = "source_subfolder"
    conflict_policy: str = "skip"
    output_subfolder: str = OUTPUT_SUBFOLDER
    output_dir: Path | None = None
    profile_id: str = "share"
    max_height: int | None = None
    _profile_explicit: bool = field(default=False, init=False, repr=False, compare=False)

    def __init__(
        self,
        crf: int = DEFAULT_CRF,
        preset: str = DEFAULT_PRESET,
        audio_bitrate: str = DEFAULT_AUDIO_BITRATE,
        output_mode: str = "source_subfolder",
        conflict_policy: str = "skip",
        output_subfolder: str = OUTPUT_SUBFOLDER,
        output_dir: Path | None = None,
        profile_id: str | object = _PROFILE_UNSET,
        max_height: int | None = None,
    ) -> None:
        self.crf = crf
        self.preset = preset
        self.audio_bitrate = audio_bitrate
        self.output_mode = output_mode
        self.conflict_policy = conflict_policy
        self.output_subfolder = output_subfolder
        self.output_dir = output_dir
        self.profile_id = "share" if profile_id is _PROFILE_UNSET else str(profile_id or "share")
        self.max_height = max_height
        self._profile_explicit = profile_id is not _PROFILE_UNSET

    def normalized(self) -> "CompressionOptions":
        crf = parse_crf(self.crf)
        preset = self.preset if self.preset in PRESETS else DEFAULT_PRESET
        audio_bitrate = normalize_audio_bitrate(self.audio_bitrate)
        max_height = _normalize_max_height(self.max_height)
        profile_id = str(self.profile_id or "share").strip().lower() or "share"
        # Pre-v1.5 callers supplied advanced values without a profile id. Keep
        # that API useful by treating non-default share settings as custom.
        if not self._profile_explicit and profile_id == "share" and (
            crf != DEFAULT_CRF
            or preset != DEFAULT_PRESET
            or audio_bitrate != DEFAULT_AUDIO_BITRATE
            or max_height is not None
        ):
            profile_id = "custom"
        normalized = CompressionOptions(
            crf=crf,
            preset=preset,
            audio_bitrate=audio_bitrate,
            output_mode=self.output_mode if self.output_mode in OUTPUT_MODES else "source_subfolder",
            conflict_policy=(
                self.conflict_policy if self.conflict_policy in CONFLICT_POLICIES else "skip"
            ),
            output_subfolder=self.output_subfolder or OUTPUT_SUBFOLDER,
            output_dir=Path(self.output_dir) if self.output_dir else None,
            profile_id=profile_id,
            max_height=max_height,
        )
        normalized._profile_explicit = self._profile_explicit
        return normalized


@dataclass
class VideoItem:
    path: Path
    size: int = 0
    duration: int = 0
    output_path: Path | None = None
    status: str = "待压缩"
    progress: int = 0
    selected: bool = True

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        if not self.size and self.path.exists():
            self.size = self.path.stat().st_size


@dataclass
class BatchSummary:
    success_count: int = 0
    fail_count: int = 0
    skipped_count: int = 0
    not_smaller_count: int = 0
    cancelled: bool = False
    original_size: int = 0
    output_size: int = 0
    results: list[CompressionResult] = field(default_factory=list)

    @property
    def saved_size(self) -> int:
        return max(0, self.original_size - self.output_size)


class CancelToken:
    def __init__(self) -> None:
        self._cancel_event = threading.Event()
        self._pause_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def pause(self) -> None:
        self._pause_event.set()

    def resume(self) -> None:
        self._pause_event.clear()

    def is_pause_requested(self) -> bool:
        return self._pause_event.is_set()


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _candidate_binary_names(name: str) -> tuple[str, ...]:
    if os.name == "nt":
        return (f"{name}.exe", name)
    return (name, f"{name}.exe")


def resolve_tool_path(tool_name: str, app_base: Path | None = None) -> str | None:
    base = Path(app_base) if app_base else app_dir()
    candidates: list[Path] = []

    search_bases = [base]
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        search_bases.append(Path(sys._MEIPASS))  # type: ignore[attr-defined]

    for root in search_bases:
        for binary in _candidate_binary_names(tool_name):
            candidates.extend(
                [
                    root / "ffmpeg" / "bin" / binary,
                    root / "ffmpeg" / binary,
                    root / binary,
                ]
            )

    for candidate in candidates:
        if candidate.is_file() and (os.name == "nt" or os.access(candidate, os.X_OK)):
            return str(candidate)

    resolved = shutil.which(tool_name)
    return resolved


def resolve_ffmpeg_path(app_base: Path | None = None) -> str | None:
    return resolve_tool_path("ffmpeg", app_base)


def resolve_ffprobe_path(app_base: Path | None = None) -> str | None:
    return resolve_tool_path("ffprobe", app_base)


def is_video_file(path: Path | str) -> bool:
    return Path(path).suffix.lower() in VIDEO_EXTENSIONS


def is_generated_output(path: Path | str) -> bool:
    video_path = Path(path)
    lowered_parts = [part.lower() for part in video_path.parts]
    if OUTPUT_SUBFOLDER.lower() in lowered_parts:
        return True
    stem = video_path.stem.lower()
    return stem.endswith("_compressed") or "_compressed_" in stem


def iter_video_paths(root: Path) -> Iterable[Path]:
    if root.is_file():
        if is_video_file(root) and not is_generated_output(root):
            yield root
        return

    if not root.is_dir():
        return

    def ignore_walk_error(error: OSError) -> None:
        return None

    for dirpath, dirnames, filenames in os.walk(root, onerror=ignore_walk_error):
        dirnames[:] = [
            dirname
            for dirname in dirnames
            if dirname.lower() != OUTPUT_SUBFOLDER.lower()
        ]
        current = Path(dirpath)
        for filename in filenames:
            candidate = current / filename
            if is_video_file(candidate) and not is_generated_output(candidate):
                yield candidate


def scan_videos(path: Path | str, include_duration: bool = False, ffprobe: str | None = None) -> list[VideoItem]:
    root = Path(path).expanduser()
    files = iter_video_paths(root)

    seen: set[Path] = set()
    videos: list[VideoItem] = []
    probe = ffprobe or resolve_ffprobe_path()
    for file_path in files:
        try:
            resolved = file_path.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        duration = get_video_duration_ms(probe, resolved) if include_duration and probe else 0
        videos.append(VideoItem(path=resolved, duration=duration))

    return sorted(videos, key=lambda item: str(item.path).lower())


def _output_candidate(
    video: VideoItem | Path | str,
    options: CompressionOptions,
    index: int = 1,
) -> Path:
    opts = options.normalized()
    video_path = Path(video.path if isinstance(video, VideoItem) else video)

    if opts.output_mode == "custom" and opts.output_dir:
        output_dir = opts.output_dir
    else:
        output_dir = video_path.parent / opts.output_subfolder

    suffix = "" if index == 1 else f"_{index}"
    return output_dir / f"{video_path.stem}_compressed{suffix}.mp4"


def build_output_path(video: VideoItem | Path | str, options: CompressionOptions) -> Path:
    opts = options.normalized()
    base = _output_candidate(video, opts)
    if opts.conflict_policy != "rename" or not base.exists():
        return base

    index = 2
    while True:
        candidate = _output_candidate(video, opts, index)
        if not candidate.exists():
            return candidate
        index += 1


def _path_key(path: Path | str) -> str:
    try:
        resolved = Path(path).expanduser().resolve(strict=False)
    except OSError:
        resolved = Path(os.path.abspath(Path(path).expanduser()))
    return os.path.normcase(str(resolved))


def _reserve_batch_output_paths(
    videos: Iterable[VideoItem | Path | str],
    options: CompressionOptions,
) -> list[Path]:
    opts = options.normalized()
    items = list(videos)
    input_paths = {
        _path_key(video.path if isinstance(video, VideoItem) else video)
        for video in items
    }
    reserved: set[str] = set()
    paths: list[Path] = []
    for video in items:
        index = 1
        while True:
            candidate = _output_candidate(video, opts, index)
            key = _path_key(candidate)
            conflicts_on_disk = opts.conflict_policy == "rename" and candidate.exists()
            if key not in input_paths and key not in reserved and not conflicts_on_disk:
                reserved.add(key)
                paths.append(candidate)
                break
            index += 1
    return paths


def temporary_output_path(final_path: Path) -> Path:
    token = uuid.uuid4().hex
    return final_path.with_name(f"{final_path.stem}.{os.getpid()}.{token}.part{final_path.suffix}")


def parse_crf(value: Any) -> int:
    try:
        crf = int(value)
    except (TypeError, ValueError):
        return DEFAULT_CRF
    return max(0, min(51, crf))


def normalize_audio_bitrate(value: Any) -> str:
    text = str(value or DEFAULT_AUDIO_BITRATE).strip().lower()
    if re.fullmatch(r"\d+k", text):
        return text
    return DEFAULT_AUDIO_BITRATE


def _normalize_max_height(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        height = int(value)
    except (TypeError, ValueError):
        return None
    return height if height > 0 else None


def _profile_for_options(options: CompressionOptions) -> CompressionProfile:
    normalized = options.normalized()
    if normalized.profile_id in PROFILES:
        return get_profile(normalized.profile_id)
    return CompressionProfile(
        "custom",
        normalized.crf,
        normalized.preset,
        normalized.audio_bitrate,
        normalized.max_height,
    )


def parse_selection(confirm: str, total: int) -> list[int] | None:
    text = (confirm or "").strip()
    if text == "" or text.lower() == "y":
        return list(range(total))
    if text.lower() == "n":
        return None

    if "-" in text:
        try:
            start_text, end_text = [part.strip() for part in text.split("-", 1)]
            start, end = int(start_text), int(end_text)
            if 1 <= start <= end <= total:
                return list(range(start - 1, end))
        except ValueError:
            return []
        return []

    if "," in text:
        try:
            indices = [int(part.strip()) for part in text.split(",") if part.strip()]
            if indices and all(1 <= index <= total for index in indices):
                return [index - 1 for index in indices]
        except ValueError:
            return []
        return []

    try:
        index = int(text)
        if 1 <= index <= total:
            return [index - 1]
    except ValueError:
        return []
    return []


def get_video_duration_ms(ffprobe_or_ffmpeg: str | None, video_path: Path | str) -> int:
    if not ffprobe_or_ffmpeg:
        return 0

    tool = str(ffprobe_or_ffmpeg)
    video = str(video_path)

    if "ffprobe" in Path(tool).name.lower():
        try:
            result = subprocess.run(
                [
                    tool,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "json",
                    video,
                ],
                capture_output=True,
                timeout=30,
            )
            payload = json.loads((result.stdout or b"{}").decode("utf-8", errors="replace"))
            seconds = float(payload.get("format", {}).get("duration", 0) or 0)
            return max(0, int(seconds * 1000))
        except Exception:
            pass

    try:
        result = subprocess.run([tool, "-i", video, "-hide_banner"], capture_output=True, timeout=30)
        duration_text: bytes | str = (result.stderr or b"") + (result.stdout or b"")
        if isinstance(duration_text, bytes):
            duration_text = duration_text.decode("utf-8", errors="replace")
        match = re.search(r"Duration: (\d+):(\d+):(\d+)\.(\d+)", duration_text)
        if match:
            hours, minutes, seconds, millis = match.groups()
            return (
                int(hours) * 3600000
                + int(minutes) * 60000
                + int(seconds) * 1000
                + int(millis.ljust(3, "0")[:3])
            )
    except Exception:
        pass
    return 0


def build_ffmpeg_command(
    ffmpeg: str,
    video_path: Path | str,
    output_path: Path | str,
    options: CompressionOptions | CompressionProfile,
    media: MediaInfo | None = None,
) -> list[str]:
    profile = _profile_for_options(options) if isinstance(options, CompressionOptions) else options
    return _build_v15_ffmpeg_command(ffmpeg, video_path, output_path, profile, media)


def _emit(callbacks: dict[str, Callable[..., None]] | None, name: str, *args: Any, **kwargs: Any) -> None:
    try:
        callback = callbacks.get(name) if callbacks else None
        if callback:
            callback(*args, **kwargs)
    except Exception as exc:
        record_event(logging.ERROR, "callback", callback=name, error=str(exc))


def _cancelled(cancel_token: CancelToken | Callable[[], bool] | None) -> bool:
    if cancel_token is None:
        return False
    if isinstance(cancel_token, CancelToken):
        return cancel_token.is_cancelled()
    return bool(cancel_token())


def _pause_requested(cancel_token: Any) -> bool:
    if cancel_token is None:
        return False
    is_pause_requested = getattr(cancel_token, "is_pause_requested", None)
    return bool(is_pause_requested()) if callable(is_pause_requested) else False


def _result_failure(
    error: str,
    code: str,
    *,
    original_size: int = 0,
    output_path: Path | None = None,
    diagnostic: str = "",
    input_info: MediaInfo | None = None,
    output_info: MediaInfo | None = None,
) -> CompressionResult:
    return CompressionResult(
        original_size=original_size,
        output_path=output_path,
        error=error,
        state=JobState.FAILED,
        error_code=code,
        diagnostic=diagnostic or error,
        input_info=input_info,
        output_info=output_info,
    )


def _prepare_output_directory(directory: Path) -> tuple[bool, str]:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".write-test-", dir=directory, delete=True):
            pass
        return True, ""
    except OSError as exc:
        return False, str(exc)


def _available_disk_space(directory: Path) -> int | None:
    try:
        usage = shutil.disk_usage(directory)
        return int(getattr(usage, "free", usage[2]))
    except OSError:
        return None


def _read_pipe(stream: Any, channel: str, events: queue.Queue[tuple[str, str]]) -> None:
    if stream is None:
        return
    try:
        while True:
            line = stream.readline()
            if not line:
                return
            events.put((channel, str(line).strip()))
    except Exception as exc:
        record_event(logging.WARNING, "pipe_reader", channel=channel, error=str(exc))


def _consume_process_events(
    events: queue.Queue[tuple[str, str]],
    item: VideoItem,
    callbacks: dict[str, Callable[..., None]] | None,
    duration_ms: int,
    error_lines: list[str],
) -> None:
    while True:
        try:
            channel, line = events.get_nowait()
        except queue.Empty:
            return
        _handle_process_event(channel, line, item, callbacks, duration_ms, error_lines)


def _handle_process_event(
    channel: str,
    line: str,
    item: VideoItem,
    callbacks: dict[str, Callable[..., None]] | None,
    duration_ms: int,
    error_lines: list[str],
) -> None:
    if not line:
        return
    if channel == "stderr":
        error_lines.append(line)
        if len(error_lines) > 30:
            del error_lines[:10]
        return
    _emit(callbacks, "log", item, line)
    if line.startswith(("out_time_ms=", "out_time_us=")):
        try:
            elapsed_us = int(line.split("=", 1)[1])
            percent = min(98, int(elapsed_us / (duration_ms * 1000) * 100)) if duration_ms else 0
            _emit(callbacks, "progress", item, percent, elapsed_us / 1_000_000)
        except ValueError:
            pass


def compress_video(
    video: VideoItem | Path | str,
    options: CompressionOptions,
    callbacks: dict[str, Callable[..., None]] | None = None,
    cancel_token: CancelToken | Callable[[], bool] | None = None,
    ffmpeg: str | None = None,
    ffprobe: str | None = None,
    *,
    reserved_output_path: Path | str | None = None,
) -> CompressionResult:
    opts = options.normalized()
    item = video if isinstance(video, VideoItem) else VideoItem(Path(video))
    final_path: Path | None = None
    temp_path: Path | None = None
    controller: ProcessController | None = None
    process: Any = None
    reader_threads: list[threading.Thread] = []
    event_queue: queue.Queue[tuple[str, str]] = queue.Queue()
    error_lines: list[str] = []
    source_size = 0
    input_info: MediaInfo | None = None
    verified_output: MediaInfo | None = None
    keep_temp = False
    disk_warning = ""
    pause_applied = False
    pause_available_emitted = False
    try:
        if not item.path.exists():
            return _result_failure(f"文件不存在: {item.path}", "input_missing")
        source_size = item.path.stat().st_size
        if not is_video_file(item.path):
            return _result_failure(
                f"不是支持的视频文件: {item.path.name}", "unsupported_input", original_size=source_size
            )

        final_path = Path(reserved_output_path) if reserved_output_path is not None else build_output_path(item, opts)
        item.output_path = final_path
        if _path_key(final_path) == _path_key(item.path):
            return _result_failure(
                "输出路径不能与源视频相同",
                "unsafe_output_path",
                original_size=source_size,
                output_path=final_path,
                diagnostic=f"input={item.path} output={final_path}",
            )
        writable, directory_error = _prepare_output_directory(final_path.parent)
        if not writable:
            return _result_failure(
                f"输出目录不可写: {final_path.parent}",
                "output_directory_unwritable",
                original_size=source_size,
                output_path=final_path,
                diagnostic=directory_error,
            )
        free_space = _available_disk_space(final_path.parent)
        if free_space is not None and free_space < source_size:
            return _result_failure(
                "输出磁盘空间不足",
                "insufficient_disk_space",
                original_size=source_size,
                output_path=final_path,
                diagnostic=f"free={free_space} required_at_least={source_size}",
            )
        if free_space is not None and free_space < source_size * 2:
            disk_warning = f"disk space is low: free={free_space} input={source_size}"
            record_event(logging.WARNING, "disk_space", path=final_path.parent, warning=disk_warning)

        if final_path.exists() and opts.conflict_policy == "skip":
            return CompressionResult(
                skipped=True,
                original_size=source_size,
                output_size=final_path.stat().st_size,
                output_path=final_path,
                error="已存在压缩后文件，已跳过",
                state=JobState.SKIPPED,
            )

        ffmpeg_path = ffmpeg or resolve_ffmpeg_path()
        ffprobe_path = ffprobe or resolve_ffprobe_path()
        if not ffmpeg_path:
            return _result_failure("未找到 FFmpeg", "ffmpeg_unavailable", original_size=source_size, output_path=final_path)
        if not ffprobe_path:
            return _result_failure("未找到 FFprobe", "ffprobe_unavailable", original_size=source_size, output_path=final_path)
        if _cancelled(cancel_token):
            return CompressionResult(
                cancelled=True,
                original_size=source_size,
                output_path=final_path,
                state=JobState.CANCELLING,
            )

        _emit(callbacks, "status", item, "分析中")
        record_event(logging.INFO, "probe_input", path=item.path, ffprobe=ffprobe_path)
        input_info = probe_media(item.path, ffprobe_path, cancel_token=cancel_token)
        if input_info.error_code:
            if input_info.state == JobState.CANCELLING:
                return CompressionResult(
                    cancelled=True,
                    original_size=source_size,
                    output_path=final_path,
                    state=JobState.CANCELLING,
                    input_info=input_info,
                    error_code=input_info.error_code,
                    diagnostic=input_info.diagnostic,
                )
            return _result_failure(
                "无法探测输入视频",
                input_info.error_code,
                original_size=source_size,
                output_path=final_path,
                diagnostic=input_info.diagnostic,
                input_info=input_info,
            )

        temp_path = temporary_output_path(final_path)
        while temp_path.exists():
            temp_path = temporary_output_path(final_path)
        command = build_ffmpeg_command(ffmpeg_path, item.path, temp_path, opts, input_info)
        _emit(callbacks, "status", item, "压缩中")
        _emit(callbacks, "progress", item, 0, 0)
        record_event(logging.INFO, "encode_start", input=item.path, temp=temp_path, ffmpeg=ffmpeg_path)
        controller = ProcessController()
        process = controller.start(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
        )
        for channel, stream in (("stdout", getattr(process, "stdout", None)), ("stderr", getattr(process, "stderr", None))):
            thread = threading.Thread(target=_read_pipe, args=(stream, channel, event_queue), daemon=True)
            thread.start()
            reader_threads.append(thread)

        cancel_requested = False
        returncode: int | None = None
        _emit(callbacks, "pause_available", item, True)
        pause_available_emitted = True
        while process.poll() is None:
            _consume_process_events(event_queue, item, callbacks, input_info.duration_ms, error_lines)
            if _cancelled(cancel_token) and not cancel_requested:
                cancel_requested = True
                controller.request_cancel()
                record_event(logging.INFO, "encode_cancel_requested", input=item.path)
                # Do not wait for poll() to change by itself: terminate() may be
                # ignored or spend an unbounded time flushing an MP4 trailer.
                # ProcessController.wait() applies the bounded grace period and
                # escalates to kill while the pipe-reader threads keep draining.
                returncode = controller.wait()
                break
            wants_pause = _pause_requested(cancel_token)
            if wants_pause and not pause_applied:
                if controller.pause():
                    pause_applied = True
                    _emit(callbacks, "status", item, "已暂停")
                    _emit(callbacks, "pause_changed", item, True)
                    record_event(logging.INFO, "encode_paused", input=item.path)
            elif not wants_pause and pause_applied:
                if controller.resume():
                    pause_applied = False
                    _emit(callbacks, "status", item, "压缩中")
                    _emit(callbacks, "pause_changed", item, False)
                    record_event(logging.INFO, "encode_resumed", input=item.path)
            try:
                channel, line = event_queue.get(timeout=0.1)
                _handle_process_event(channel, line, item, callbacks, input_info.duration_ms, error_lines)
                event_queue.task_done()
            except queue.Empty:
                pass
        if returncode is None:
            returncode = controller.wait()
        for thread in reader_threads:
            thread.join(timeout=0.2)
        _consume_process_events(event_queue, item, callbacks, input_info.duration_ms, error_lines)
        if pause_applied:
            _emit(callbacks, "pause_changed", item, False)
            pause_applied = False
        if pause_available_emitted:
            _emit(callbacks, "pause_available", item, False)
            pause_available_emitted = False
        if _cancelled(cancel_token):
            _safe_unlink(temp_path)
            return CompressionResult(
                cancelled=True,
                original_size=source_size,
                output_path=final_path,
                state=JobState.CANCELLING,
                input_info=input_info,
            )

        if returncode != 0 or not temp_path.exists():
            _safe_unlink(temp_path)
            message = build_error_message(returncode, error_lines)
            record_event(logging.ERROR, "encode_failed", input=item.path, ffmpeg=ffmpeg_path, error=message)
            return _result_failure(
                message,
                "encode_failed",
                original_size=source_size,
                output_path=final_path,
                input_info=input_info,
            )

        verification = verify_output(
            input_info,
            temp_path,
            ffprobe_path,
            ffmpeg=ffmpeg_path,
            cancel_token=cancel_token,
        )
        verified_output = verification.output_info
        if not verification.valid:
            _safe_unlink(temp_path)
            if verification.error_code == "verification_cancelled":
                return CompressionResult(
                    cancelled=True,
                    original_size=source_size,
                    output_path=final_path,
                    state=JobState.CANCELLING,
                    input_info=input_info,
                    output_info=verified_output,
                    error_code=verification.error_code,
                    diagnostic=verification.diagnostic,
                )
            record_event(logging.ERROR, "verify_failed", temp=temp_path, error=verification.diagnostic)
            return _result_failure(
                "输出验证失败",
                verification.error_code or "verification_failed",
                original_size=source_size,
                output_path=final_path,
                diagnostic=verification.diagnostic,
                input_info=input_info,
                output_info=verified_output,
            )

        keep_temp = True
        commit_path = final_path
        try:
            if opts.conflict_policy == "overwrite":
                os.replace(temp_path, commit_path)
            else:
                while True:
                    try:
                        os.link(temp_path, commit_path)
                        _safe_unlink(temp_path)
                        break
                    except FileExistsError:
                        if opts.conflict_policy == "skip":
                            _safe_unlink(temp_path)
                            return CompressionResult(
                                skipped=True,
                                original_size=source_size,
                                output_size=commit_path.stat().st_size,
                                output_path=commit_path,
                                error="已存在压缩后文件，已跳过",
                                state=JobState.SKIPPED,
                                input_info=input_info,
                                output_info=verified_output,
                            )
                        commit_path = build_output_path(item, opts)
        except OSError as exc:
            diagnostic = f"verified temporary output retained at {temp_path}: {exc}"
            record_event(logging.ERROR, "commit_failed", temp=temp_path, output=commit_path, error=str(exc))
            return _result_failure(
                "无法提交压缩输出",
                "commit_failed",
                original_size=source_size,
                output_path=commit_path,
                diagnostic=diagnostic,
                input_info=input_info,
                output_info=verified_output,
            )

        keep_temp = False
        item.output_path = commit_path
        output_size = commit_path.stat().st_size
        result_state = JobState.NOT_SMALLER if output_size >= source_size else JobState.SUCCEEDED
        _emit(callbacks, "progress", item, 100, input_info.duration_ms / 1000 if input_info.duration_ms else 0)
        _emit(callbacks, "status", item, "未变小" if result_state == JobState.NOT_SMALLER else "完成")
        return CompressionResult(
            success=True,
            original_size=source_size,
            output_size=output_size,
            output_path=commit_path,
            state=result_state,
            diagnostic=disk_warning,
            input_info=input_info,
            output_info=verified_output,
        )
    except Exception as exc:
        record_event(logging.ERROR, "runtime_exception", input=item.path, error=str(exc))
        if temp_path is not None and not keep_temp:
            _safe_unlink(temp_path)
        return _result_failure(
            str(exc),
            "runtime_exception",
            original_size=source_size,
            output_path=final_path,
            input_info=input_info,
            output_info=verified_output,
        )
    finally:
        if pause_applied:
            _emit(callbacks, "pause_changed", item, False)
        if pause_available_emitted:
            _emit(callbacks, "pause_available", item, False)
        if controller is not None and process is not None and process.poll() is None:
            try:
                controller.request_cancel()
                controller.wait()
            except Exception as exc:
                record_event(logging.WARNING, "process_cleanup", error=str(exc))
        for thread in reader_threads:
            if thread.is_alive():
                thread.join(timeout=0.1)
        close_process_streams(process)


def compress_batch(
    videos: Iterable[VideoItem | Path | str],
    options: CompressionOptions,
    callbacks: dict[str, Callable[..., None]] | None = None,
    cancel_token: CancelToken | Callable[[], bool] | None = None,
    ffmpeg: str | None = None,
    ffprobe: str | None = None,
) -> BatchSummary:
    items = list(videos)
    reserved_paths = _reserve_batch_output_paths(items, options)
    summary = BatchSummary()
    total = len(items)

    for index, (item, reserved_path) in enumerate(zip(items, reserved_paths), 1):
        if _cancelled(cancel_token):
            summary.cancelled = True
            break

        _emit(callbacks, "item_start", item, index, total)
        result = compress_video(
            item,
            options,
            callbacks,
            cancel_token,
            ffmpeg,
            ffprobe,
            reserved_output_path=reserved_path,
        )
        summary.results.append(result)

        if result.cancelled:
            summary.cancelled = True
            _emit(callbacks, "item_done", item, result, index, total)
            break
        if result.skipped:
            summary.skipped_count += 1
        elif result.success and result.state == JobState.NOT_SMALLER:
            summary.not_smaller_count += 1
        elif result.success:
            summary.success_count += 1
            summary.original_size += result.original_size
            summary.output_size += result.output_size
        else:
            summary.fail_count += 1

        _emit(callbacks, "item_done", item, result, index, total)
        _emit(callbacks, "batch_progress", summary, index, total)

    return summary


def bytes_to_mb(size: int) -> float:
    return size / 1024 / 1024


def format_size(size: int) -> str:
    if size >= 1024**3:
        return f"{size / 1024**3:.2f} GB"
    return f"{size / 1024**2:.1f} MB"


def build_error_message(returncode: int | None, error_lines: list[str]) -> str:
    detail = "\n".join(error_lines[-5:]).strip()
    if detail:
        return f"FFmpeg 压缩失败，退出码 {returncode}: {detail}"
    return f"FFmpeg 压缩失败，退出码 {returncode}"


def _safe_unlink(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass
