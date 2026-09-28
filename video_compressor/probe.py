"""Structured ffprobe integration."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .models import JobState, MediaInfo, ToolchainReport
from .runner import run_bounded_process


def _text(value: bytes | str | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _bit_depth(stream: dict[str, Any]) -> int:
    depth = _to_int(stream.get("bits_per_raw_sample") or stream.get("bits_per_sample"))
    if depth:
        return depth
    match = re.search(r"(?:p|yuv)(\d{2})(?:le|be)?$", str(stream.get("pix_fmt") or ""))
    return int(match.group(1)) if match else 8 if stream.get("pix_fmt") else 0


def _cancelled(cancel_token: Any) -> bool:
    if cancel_token is None:
        return False
    is_cancelled = getattr(cancel_token, "is_cancelled", None)
    return bool(is_cancelled()) if callable(is_cancelled) else bool(cancel_token())


def probe_media(
    path: Path | str,
    toolchain_or_ffprobe: ToolchainReport | str,
    cancel_token: Any = None,
    timeout: float = 30,
) -> MediaInfo:
    source = Path(path)
    if _cancelled(cancel_token):
        return MediaInfo(path=source, state=JobState.CANCELLING, error_code="probe_cancelled")

    ffprobe = (
        toolchain_or_ffprobe.ffprobe_path
        if isinstance(toolchain_or_ffprobe, ToolchainReport)
        else toolchain_or_ffprobe
    )
    if not ffprobe:
        return MediaInfo(
            path=source,
            state=JobState.FAILED,
            error_code="ffprobe_unavailable",
            diagnostic="ffprobe not configured",
        )
    try:
        result = run_bounded_process(
            [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(source)],
            cancel_token=cancel_token,
            timeout=timeout,
        )
        if result.cancelled or _cancelled(cancel_token):
            return MediaInfo(path=source, state=JobState.CANCELLING, error_code="probe_cancelled")
        if result.timed_out:
            return MediaInfo(
                path=source,
                state=JobState.FAILED,
                error_code="probe_timeout",
                diagnostic=f"ffprobe exceeded {timeout} seconds",
            )
        if result.returncode != 0:
            message = _text(result.stderr).strip() or f"ffprobe exited with {result.returncode}"
            return MediaInfo(path=source, state=JobState.FAILED, error_code="probe_failed", diagnostic=message)
        payload = json.loads(_text(result.stdout) or "{}")
        streams = payload.get("streams") or []
        video = next((stream for stream in streams if stream.get("codec_type") == "video"), {})
        media_format = payload.get("format") or {}
        try:
            duration_ms = max(0, int(float(media_format.get("duration") or 0) * 1000))
        except (TypeError, ValueError):
            duration_ms = 0
        size_bytes = source.stat().st_size if source.exists() else _to_int(media_format.get("size"))
        return MediaInfo(
            path=source,
            duration_ms=duration_ms,
            size_bytes=size_bytes,
            width=_to_int(video.get("width")),
            height=_to_int(video.get("height")),
            video_codec=str(video.get("codec_name") or ""),
            pixel_format=str(video.get("pix_fmt") or ""),
            bit_depth=_bit_depth(video),
            color_transfer=str(video.get("color_transfer") or ""),
            color_primaries=str(video.get("color_primaries") or ""),
            has_audio=any(stream.get("codec_type") == "audio" for stream in streams),
            state=JobState.PROBING,
        )
    except Exception as exc:
        return MediaInfo(path=source, state=JobState.FAILED, error_code="probe_failed", diagnostic=str(exc))
