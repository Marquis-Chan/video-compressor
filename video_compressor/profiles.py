"""The fixed, user-facing compression profiles for version 1.5."""

from __future__ import annotations

from .models import CompressionProfile


PROFILE_SHARE = CompressionProfile("share", 25, "fast", "128k", None)
PROFILE_QUALITY = CompressionProfile("quality", 23, "medium", "160k", None)
PROFILE_SMALL = CompressionProfile("small", 28, "fast", "96k", 1080)

PROFILES = {
    "share": PROFILE_SHARE,
    "quality": PROFILE_QUALITY,
    "small": PROFILE_SMALL,
}


def get_profile(name: str) -> CompressionProfile:
    try:
        return PROFILES[name]
    except KeyError as exc:
        raise ValueError(f"Unknown compression profile: {name}") from exc

