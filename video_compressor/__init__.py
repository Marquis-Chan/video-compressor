"""Version 1.5 compression primitives exposed independently from the UI."""

from .models import CompressionProfile, CompressionResult, JobState, MediaInfo, ToolchainReport
from .profiles import PROFILE_QUALITY, PROFILE_SHARE, PROFILE_SMALL, PROFILES, get_profile
from .runner import ProcessController
from .verify import VerificationResult, verify_output

__all__ = [
    "CompressionProfile",
    "CompressionResult",
    "JobState",
    "MediaInfo",
    "ToolchainReport",
    "PROFILE_QUALITY",
    "PROFILE_SHARE",
    "PROFILE_SMALL",
    "PROFILES",
    "get_profile",
    "ProcessController",
    "VerificationResult",
    "verify_output",
]
