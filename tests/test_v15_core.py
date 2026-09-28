# -*- coding: utf-8 -*-

import json
import tempfile
import unittest
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from video_compressor.models import (
    CompressionProfile,
    CompressionResult,
    JobState,
    MediaInfo,
    ToolchainReport,
)
from video_compressor.probe import probe_media
from video_compressor.profiles import (
    PROFILE_QUALITY,
    PROFILE_SHARE,
    PROFILE_SMALL,
    PROFILES,
    get_profile,
)
from video_compressor.jobs import build_ffmpeg_command
from video_compressor.runner import ProcessRunResult
from video_compressor.toolchain import validate_toolchain


class ModelAndProfileTests(unittest.TestCase):
    def test_job_state_and_media_model(self):
        info = MediaInfo(
            path=Path("input.mp4"),
            duration_ms=2500,
            size_bytes=1234,
            width=1921,
            height=1081,
            video_codec="hevc",
            pixel_format="yuv420p10le",
            has_audio=False,
            color_transfer="smpte2084",
            color_primaries="bt2020",
        )

        self.assertEqual(JobState.NOT_SMALLER.value, "not_smaller")
        self.assertEqual(JobState.PROBING.value, "probing")
        self.assertEqual(JobState.CANCELLING.value, "cancelling")
        self.assertEqual(JobState.SUCCEEDED.value, "succeeded")
        self.assertEqual(JobState.NOT_RUN.value, "not_run")
        self.assertIs(JobState.PROBED, JobState.PROBING)
        self.assertIs(JobState.COMPLETED, JobState.SUCCEEDED)
        self.assertIs(JobState.CANCELLED, JobState.CANCELLING)
        self.assertTrue(info.is_hdr)
        self.assertFalse(info.has_audio)

    def test_profile_exposes_v15_fields_without_breaking_legacy_constructor(self):
        profile = CompressionProfile("legacy", 24, "fast", "128k", None)

        self.assertEqual(profile.name, "legacy")
        self.assertEqual(profile.id, "legacy")
        self.assertEqual(profile.label, "legacy")
        self.assertTrue(profile.force_sdr)
        self.assertEqual(profile.video_codec, "libx264")
        self.assertEqual(profile.pixel_format, "yuv420p")

    def test_fixed_profiles_match_v15_contract(self):
        self.assertEqual(
            PROFILES,
            {
                "share": CompressionProfile("share", 25, "fast", "128k", None),
                "quality": CompressionProfile("quality", 23, "medium", "160k", None),
                "small": CompressionProfile("small", 28, "fast", "96k", 1080),
            },
        )
        self.assertIs(get_profile("share"), PROFILES["share"])
        with self.assertRaises(ValueError):
            get_profile("unknown")

        self.assertEqual(PROFILE_SHARE, PROFILES["share"])
        self.assertEqual(PROFILE_QUALITY, PROFILES["quality"])
        self.assertEqual(PROFILE_SMALL, PROFILES["small"])

    def test_compression_result_keeps_legacy_fields_and_adds_diagnostics(self):
        result = CompressionResult(success=False, error="bad", error_code="encode_failed")

        self.assertFalse(result.success)
        self.assertEqual(result.error, "bad")
        self.assertEqual(result.error_code, "encode_failed")
        self.assertEqual(result.state, JobState.FAILED)
        self.assertIsNone(result.input_info)
        self.assertIsNone(result.output_info)


class ProbeAndToolchainTests(unittest.TestCase):
    def test_probe_media_parses_ffprobe_json_and_hdr_metadata(self):
        payload = {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "hevc",
                    "width": 3840,
                    "height": 2160,
                    "pix_fmt": "yuv420p10le",
                    "bits_per_raw_sample": "10",
                    "color_transfer": "smpte2084",
                    "color_primaries": "bt2020",
                },
                {"codec_type": "audio", "codec_name": "aac"},
            ],
            "format": {"duration": "2.75", "size": "999"},
        }
        completed = ProcessRunResult(returncode=0, stdout=json.dumps(payload).encode())

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "input.mp4"
            source.write_bytes(b"x" * 123)
            with patch("video_compressor.probe.run_bounded_process", return_value=completed):
                info = probe_media(source, "ffprobe")

        self.assertEqual(info.video_codec, "hevc")
        self.assertEqual((info.width, info.height), (3840, 2160))
        self.assertEqual(info.pixel_format, "yuv420p10le")
        self.assertEqual(info.bit_depth, 10)
        self.assertTrue(info.is_hdr)
        self.assertTrue(info.has_audio)
        self.assertEqual(info.duration_ms, 2750)
        self.assertEqual(info.size_bytes, 123)

    def test_probe_media_returns_structured_failure(self):
        with patch("video_compressor.probe.run_bounded_process", side_effect=OSError("missing")):
            info = probe_media(Path("missing.mp4"), "ffprobe")

        self.assertEqual(info.state, JobState.FAILED)
        self.assertEqual(info.error_code, "probe_failed")
        self.assertIn("missing", info.diagnostic)

    def test_probe_media_accepts_toolchain_report_and_returns_cancelled_state(self):
        report = ToolchainReport(ffprobe_path="ffprobe")

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "input.mp4"
            source.write_bytes(b"x")
            info = probe_media(source, report, cancel_token=lambda: True, timeout=1)

        self.assertEqual(info.state, JobState.CANCELLING)
        self.assertEqual(info.error_code, "probe_cancelled")

    def test_toolchain_report_exposes_v15_fields_and_legacy_ok(self):
        report = ToolchainReport(
            ready=True,
            platform="Windows",
            arch="AMD64",
            version="7.1",
            encoders={"libx264", "aac"},
            filters={"zscale", "tonemap"},
            error="",
            ffmpeg_available=True,
            ffprobe_available=True,
            libx264=True,
            aac=True,
            zscale=True,
            tonemap=True,
        )

        self.assertTrue(report.ready)
        self.assertTrue(report.ok)
        self.assertEqual(report.version, "7.1")
        self.assertIn("libx264", report.encoders)
        self.assertIn("tonemap", report.filters)

    def test_validate_toolchain_executes_and_reports_required_capabilities(self):
        def fake_run(command, **_kwargs):
            if command[0] == "ffprobe":
                return __import__("subprocess").CompletedProcess(command, 0, b"ffprobe version", b"")
            if "-encoders" in command:
                return __import__("subprocess").CompletedProcess(command, 0, b" V....D libx264\n A..... aac\n", b"")
            return __import__("subprocess").CompletedProcess(command, 0, b" .. zscale\n .. tonemap\n", b"")

        with patch("video_compressor.toolchain.subprocess.run", side_effect=fake_run):
            report = validate_toolchain("ffmpeg", "ffprobe")

        self.assertTrue(report.ok)
        self.assertTrue(report.libx264)
        self.assertTrue(report.aac)
        self.assertTrue(report.zscale)
        self.assertTrue(report.tonemap)


class CommandTests(unittest.TestCase):
    def test_command_maps_first_video_optional_audio_and_output_contract(self):
        info = MediaInfo(width=1920, height=1080, has_audio=False)
        cmd = build_ffmpeg_command(
            "ffmpeg", Path("input.mp4"), Path("output.mp4"), PROFILES["share"], info
        )

        self.assertIn("0:v:0", cmd)
        self.assertIn("0:a:0?", cmd)
        self.assertIn("libx264", cmd)
        self.assertIn("yuv420p", cmd)
        self.assertIn("+faststart", cmd)
        self.assertIn("aac", cmd)
        self.assertEqual(cmd[-1], "output.mp4")

    def test_small_profile_scales_to_even_dimensions_without_upscaling(self):
        info = MediaInfo(width=1919, height=1201, has_audio=True)
        cmd = build_ffmpeg_command(
            "ffmpeg", Path("input.mp4"), Path("output.mp4"), PROFILES["small"], info
        )
        filter_chain = cmd[cmd.index("-vf") + 1]

        self.assertIn("1080", filter_chain)
        self.assertIn("trunc", filter_chain)

    def test_hdr_command_tonemaps_pq_or_bt2020_to_bt709(self):
        for info in (
            MediaInfo(color_transfer="smpte2084"),
            MediaInfo(color_transfer="arib-std-b67"),
            MediaInfo(color_primaries="bt2020"),
        ):
            with self.subTest(info=info):
                cmd = build_ffmpeg_command(
                    "ffmpeg", Path("input.mp4"), Path("output.mp4"), PROFILES["quality"], info
                )
                filter_chain = cmd[cmd.index("-vf") + 1]
                self.assertIn("zscale", filter_chain)
                self.assertIn("tonemap", filter_chain)
                self.assertIn("bt709", filter_chain)


if __name__ == "__main__":
    unittest.main()
