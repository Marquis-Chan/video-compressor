"""FFmpeg command construction for v1.5 compression profiles."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .models import CompressionProfile, MediaInfo


def _scale_filter(max_height: int | None) -> str:
    if max_height:
        return f"scale=-2:trunc(min(ih\\,{max_height})/2)*2"
    return "scale=trunc(iw/2)*2:trunc(ih/2)*2"


def _video_filter(profile: CompressionProfile, media: MediaInfo | None) -> str:
    scale = _scale_filter(profile.max_height)
    if profile.force_sdr and media and media.is_hdr:
        return (
            f"zscale=transfer=linear:npl=100,format=gbrpf32le,{scale},"
            "tonemap=tonemap=hable:desat=0,"
            "zscale=primaries=bt709:transfer=bt709:matrix=bt709:range=tv,format=yuv420p"
        )
    return f"{scale},format={profile.pixel_format}"


def build_ffmpeg_command(
    ffmpeg: str,
    video_path: Path | str,
    output_path: Path | str,
    profile: CompressionProfile | Any,
    media: MediaInfo | None = None,
) -> list[str]:
    """Build a command from either a v1.5 profile or a legacy options object."""
    return [
        ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-xerror",
        "-i",
        str(video_path),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-c:v",
        profile.video_codec,
        "-crf",
        str(profile.crf),
        "-preset",
        profile.preset,
        "-vf",
        _video_filter(profile, media),
        "-pix_fmt",
        profile.pixel_format,
        "-c:a",
        "aac",
        "-b:a",
        profile.audio_bitrate,
        "-movflags",
        "+faststart",
        "-progress",
        "pipe:1",
        "-y",
        str(output_path),
    ]
