"""Post-encode media verification independent from UI and orchestration code."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .models import JobState, MediaInfo, ToolchainReport
from .probe import probe_media
from .runner import run_bounded_process


@dataclass(frozen=True)
class VerificationResult:
    valid: bool
    output_info: MediaInfo | None = None
    error_code: str = ""
    diagnostic: str = ""


def _failure(code: str, diagnostic: str, output_info: MediaInfo | None = None) -> VerificationResult:
    return VerificationResult(False, output_info, code, diagnostic)


def _text(value: bytes | str | None) -> str:
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""


def _ffmpeg_path(ffprobe_or_report: ToolchainReport | str, explicit: str | None) -> str | None:
    if explicit:
        return explicit
    if isinstance(ffprobe_or_report, ToolchainReport):
        return ffprobe_or_report.ffmpeg_path
    probe_path = Path(ffprobe_or_report)
    name = probe_path.name
    if "ffprobe" in name.lower():
        offset = name.lower().index("ffprobe")
        return str(probe_path.with_name(f"{name[:offset]}ffmpeg{name[offset + len('ffprobe'):]}"))
    return None


def verify_output(
    input_info: MediaInfo,
    output_path: Path | str,
    ffprobe_or_report: ToolchainReport | str,
    *,
    ffmpeg: str | None = None,
    cancel_token: object = None,
    decode_timeout: float | None = None,
) -> VerificationResult:
    path = Path(output_path)
    if not path.exists():
        return _failure("output_missing", f"output does not exist: {path}")
    if path.stat().st_size <= 0:
        return _failure("output_empty", f"output is empty: {path}")

    output_info = probe_media(path, ffprobe_or_report, cancel_token=cancel_token)
    if output_info.state == JobState.CANCELLING:
        return _failure("verification_cancelled", "output verification cancelled", output_info)
    if output_info.error_code:
        return _failure("output_probe_failed", output_info.diagnostic, output_info)
    if output_info.video_codec.lower() != "h264":
        return _failure("unexpected_video_codec", output_info.video_codec, output_info)
    if output_info.pixel_format.lower() != "yuv420p":
        return _failure("unexpected_pixel_format", output_info.pixel_format, output_info)
    if output_info.has_audio != input_info.has_audio:
        return _failure("audio_mismatch", "output audio does not match input expectation", output_info)

    tolerance_ms = max(1000, int(input_info.duration_ms * 0.01))
    if input_info.duration_ms and abs(output_info.duration_ms - input_info.duration_ms) > tolerance_ms:
        return _failure("duration_mismatch", "output duration exceeds tolerance", output_info)

    ffmpeg_path = _ffmpeg_path(ffprobe_or_report, ffmpeg)
    if not ffmpeg_path:
        return _failure("ffmpeg_unavailable", "ffmpeg not configured for full decode", output_info)
    timeout = decode_timeout
    if timeout is None:
        timeout = max(60.0, input_info.duration_ms / 1000 * 5 + 30)
    decoded = run_bounded_process(
        [
            ffmpeg_path,
            "-hide_banner",
            "-nostdin",
            "-v",
            "error",
            "-xerror",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-f",
            "null",
            "-",
        ],
        cancel_token=cancel_token,
        timeout=timeout,
    )
    if decoded.cancelled:
        return _failure("verification_cancelled", "output verification cancelled", output_info)
    if decoded.timed_out:
        return _failure("output_decode_timeout", f"full decode exceeded {timeout:g} seconds", output_info)
    if decoded.returncode != 0:
        diagnostic = _text(decoded.stderr).strip() or f"ffmpeg exited with {decoded.returncode}"
        return _failure("output_decode_failed", diagnostic, output_info)
    return VerificationResult(True, output_info)
