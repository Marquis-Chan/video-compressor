"""Data models shared by probing, planning, and compression execution."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class JobState(str, Enum):
    PENDING = "pending"
    PROBING = "probing"
    RUNNING = "running"
    CANCELLING = "cancelling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    NOT_SMALLER = "not_smaller"
    NOT_RUN = "not_run"

    # v1.4 names remain usable by callers that imported the old enum members.
    PROBED = PROBING
    COMPLETED = SUCCEEDED
    CANCELLED = CANCELLING


@dataclass(frozen=True)
class CompressionProfile:
    name: str
    crf: int
    preset: str
    audio_bitrate: str
    max_height: int | None = None
    id: str = ""
    label: str = ""
    force_sdr: bool = True
    video_codec: str = "libx264"
    pixel_format: str = "yuv420p"

    def __post_init__(self) -> None:
        if not self.id:
            object.__setattr__(self, "id", self.name)
        if not self.label:
            object.__setattr__(self, "label", self.name)


@dataclass
class MediaInfo:
    path: Path | None = None
    duration_ms: int = 0
    size_bytes: int = 0
    width: int = 0
    height: int = 0
    video_codec: str = ""
    pixel_format: str = ""
    bit_depth: int = 0
    color_transfer: str = ""
    color_primaries: str = ""
    has_audio: bool = False
    state: JobState = JobState.PENDING
    error_code: str = ""
    diagnostic: str = ""

    @property
    def pix_fmt(self) -> str:
        return self.pixel_format

    @property
    def size(self) -> int:
        return self.size_bytes

    @property
    def is_hdr(self) -> bool:
        transfer = self.color_transfer.lower()
        primaries = self.color_primaries.lower()
        return transfer in {"smpte2084", "pq", "arib-std-b67", "hlg"} or "bt2020" in primaries


@dataclass
class ToolchainReport:
    ready: bool = False
    platform: str = ""
    arch: str = ""
    version: str = ""
    encoders: set[str] | frozenset[str] = frozenset()
    filters: set[str] | frozenset[str] = frozenset()
    error: str = ""
    ffmpeg_path: str | None = None
    ffprobe_path: str | None = None
    ffmpeg_available: bool = False
    ffprobe_available: bool = False
    libx264: bool = False
    aac: bool = False
    zscale: bool = False
    tonemap: bool = False
    diagnostic: str = ""

    @property
    def ok(self) -> bool:
        return all(
            (
                self.ffmpeg_available,
                self.ffprobe_available,
                self.libx264,
                self.aac,
                self.zscale,
                self.tonemap,
            )
        )


@dataclass
class CompressionResult:
    """Keeps the v1.4 result fields while adding structured v1.5 context."""

    success: bool = False
    skipped: bool = False
    cancelled: bool = False
    original_size: int = 0
    output_size: int = 0
    output_path: Path | None = None
    error: str = ""
    state: JobState = JobState.PENDING
    error_code: str = ""
    diagnostic: str = ""
    input_info: MediaInfo | None = None
    output_info: MediaInfo | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.state, JobState):
            self.state = JobState(self.state)
        if self.state == JobState.PENDING:
            if self.success:
                self.state = JobState.COMPLETED
            elif self.cancelled:
                self.state = JobState.CANCELLED
            elif self.skipped:
                self.state = JobState.SKIPPED
            elif self.error or self.error_code:
                self.state = JobState.FAILED
