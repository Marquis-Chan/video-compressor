"""Checks the concrete FFmpeg capabilities required by the v1.5 pipeline."""

from __future__ import annotations

import shutil
import subprocess
import re
import sys

from .models import ToolchainReport
from .platform_support import runtime_arch


def _text(value: bytes | str | None) -> str:
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""


def _run(tool: str, *arguments: str) -> tuple[bool, str]:
    try:
        result = subprocess.run([tool, *arguments], capture_output=True, timeout=30, check=False)
        return result.returncode == 0, _text(result.stdout) + _text(result.stderr)
    except Exception as exc:
        return False, str(exc)


def _capability_names(text: str) -> frozenset[str]:
    names: set[str] = set()
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and re.fullmatch(r"[.A-Z]{2,}", parts[0]):
            names.add(parts[1])
    return frozenset(names)


def validate_toolchain(ffmpeg: str | None = None, ffprobe: str | None = None) -> ToolchainReport:
    ffmpeg_path = ffmpeg or shutil.which("ffmpeg")
    ffprobe_path = ffprobe or shutil.which("ffprobe")
    report = ToolchainReport(
        ffmpeg_path=ffmpeg_path,
        ffprobe_path=ffprobe_path,
        platform=sys.platform,
        arch=runtime_arch(),
    )
    diagnostics: list[str] = []

    if ffmpeg_path:
        version_ok, version = _run(ffmpeg_path, "-version")
        encoders_ok, encoders = _run(ffmpeg_path, "-hide_banner", "-encoders")
        filters_ok, filters = _run(ffmpeg_path, "-hide_banner", "-filters")
        report.ffmpeg_available = encoders_ok and filters_ok
        report.version = next((line.strip() for line in version.splitlines() if line.strip()), "")
        report.encoders = _capability_names(encoders)
        report.filters = _capability_names(filters)
        report.libx264 = "libx264" in encoders
        report.aac = "aac" in encoders.lower()
        report.zscale = "zscale" in filters
        report.tonemap = "tonemap" in filters
        if not report.ffmpeg_available:
            diagnostics.append("ffmpeg could not list encoders and filters")
        if not version_ok:
            diagnostics.append("ffmpeg could not report its version")
    else:
        diagnostics.append("ffmpeg not found")

    if ffprobe_path:
        report.ffprobe_available, detail = _run(ffprobe_path, "-version")
        if not report.ffprobe_available:
            diagnostics.append(detail or "ffprobe could not run")
    else:
        diagnostics.append("ffprobe not found")

    report.diagnostic = "; ".join(part for part in diagnostics if part)
    report.error = report.diagnostic
    report.ready = report.ok
    return report
