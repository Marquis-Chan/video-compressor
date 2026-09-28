# -*- coding: utf-8 -*-

import io
import os
import tempfile
import threading
import unittest
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from compressor_core import (
    APP_VERSION,
    BatchSummary,
    CancelToken,
    CompressionOptions,
    VideoItem,
    build_ffmpeg_command,
    build_output_path,
    compress_batch,
    compress_video,
    is_video_file,
    parse_crf,
    parse_selection,
    resolve_ffmpeg_path,
    scan_videos,
    temporary_output_path,
)
from video_compressor.models import CompressionResult, JobState, MediaInfo
from video_compressor.verify import VerificationResult


class _CompletedProcess:
    def __init__(self):
        self.stdout = io.StringIO("out_time_ms=1000000\nprogress=end\n")
        self.stderr = io.StringIO("")
        self.returncode = 0

    def poll(self):
        return self.returncode


class _WritingController:
    instances: list["_WritingController"] = []

    def __init__(self, *args, **kwargs):
        self.command: list[str] = []
        self.cancel_requested = False
        self.process = _CompletedProcess()
        type(self).instances.append(self)

    def start(self, command, **kwargs):
        self.command = list(command)
        Path(command[-1]).write_bytes(b"x")
        return self.process

    def request_cancel(self):
        self.cancel_requested = True
        return True

    def wait(self, timeout=None):
        return 0


class _SilentProcess:
    def __init__(self):
        self.stdout = _BlockingStream()
        self.stderr = io.StringIO("")
        self.returncode = None

    def poll(self):
        return self.returncode


class _BlockingStream:
    def readline(self):
        import time

        time.sleep(0.2)
        return ""


class _CancellingController(_WritingController):
    cancel_on_start = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.process = _SilentProcess()

    def start(self, command, **kwargs):
        self.command = list(command)
        if type(self).cancel_on_start:
            type(self).cancel_on_start()
        return self.process

    def wait(self, timeout=None):
        self.process.returncode = -15
        return -15

    def request_cancel(self):
        self.cancel_requested = True
        self.process.returncode = -15
        return True


class _WaitRequiredCancellationController(_WritingController):
    cancel_on_start = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.process = _SilentProcess()
        self.wait_calls = 0

    def start(self, command, **kwargs):
        self.command = list(command)
        if type(self).cancel_on_start:
            type(self).cancel_on_start()
        return self.process

    def request_cancel(self):
        self.cancel_requested = True
        return True

    def wait(self, timeout=None):
        self.wait_calls += 1
        self.process.returncode = -9
        return -9


class _PausableProcess:
    def __init__(self) -> None:
        self.stdout = io.StringIO("")
        self.stderr = io.StringIO("")
        self.returncode = None

    def poll(self):  # type: ignore[no-untyped-def]
        return self.returncode


class _PausableController:
    def __init__(self, token: CancelToken) -> None:
        self.token = token
        self.process = _PausableProcess()
        self.command: list[str] = []
        self.actions: list[str] = []

    def start(self, command, **kwargs):  # type: ignore[no-untyped-def]
        self.command = list(command)
        self.token.pause()
        return self.process

    def pause(self) -> bool:
        self.actions.append("pause")
        self.token.resume()
        return True

    def resume(self) -> bool:
        self.actions.append("resume")
        Path(self.command[-1]).write_bytes(b"encoded")
        self.process.returncode = 0
        return True

    def request_cancel(self) -> bool:
        self.actions.append("cancel")
        self.process.returncode = -15
        return True

    def wait(self, timeout=None):  # type: ignore[no-untyped-def]
        return self.process.returncode


class CoreTests(unittest.TestCase):
    def test_cancel_token_tracks_pause_independently_from_cancel(self):
        token = CancelToken()

        token.pause()

        self.assertTrue(token.is_pause_requested())
        self.assertFalse(token.is_cancelled())

        token.resume()

        self.assertFalse(token.is_pause_requested())

    def test_compress_video_applies_pause_then_resume_and_emits_states(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "input.mp4"
            source.write_bytes(b"source")
            token = CancelToken()
            controller = _PausableController(token)
            pause_events: list[tuple[str, bool]] = []
            status_events: list[str] = []
            with (
                patch(
                    "compressor_core.probe_media",
                    return_value=MediaInfo(path=source, duration_ms=1000),
                ),
                patch("compressor_core.ProcessController", return_value=controller),
                patch(
                    "compressor_core.verify_output",
                    return_value=VerificationResult(
                        True,
                        MediaInfo(video_codec="h264", pixel_format="yuv420p"),
                    ),
                ),
            ):
                result = compress_video(
                    source,
                    CompressionOptions(),
                    cancel_token=token,
                    ffmpeg="ffmpeg",
                    ffprobe="ffprobe",
                    callbacks={
                        "pause_available": lambda _item, value: pause_events.append(
                            ("available", value)
                        ),
                        "pause_changed": lambda _item, value: pause_events.append(
                            ("paused", value)
                        ),
                        "status": lambda _item, value: status_events.append(value),
                    },
                )

        self.assertTrue(result.success)
        self.assertEqual(controller.actions, ["pause", "resume"])
        self.assertEqual(
            pause_events,
            [
                ("available", True),
                ("paused", True),
                ("paused", False),
                ("available", False),
            ],
        )
        self.assertIn("已暂停", status_events)

    def test_pause_becomes_unavailable_before_output_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "input.mp4"
            source.write_bytes(b"source")
            events: list[object] = []

            def verify(*_args, **_kwargs):  # type: ignore[no-untyped-def]
                events.append("verify")
                return VerificationResult(
                    True,
                    MediaInfo(video_codec="h264", pixel_format="yuv420p"),
                )

            with (
                patch(
                    "compressor_core.probe_media",
                    return_value=MediaInfo(path=source, duration_ms=1000),
                ),
                patch("compressor_core.ProcessController", _WritingController),
                patch("compressor_core.verify_output", side_effect=verify),
            ):
                result = compress_video(
                    source,
                    CompressionOptions(),
                    ffmpeg="ffmpeg",
                    ffprobe="ffprobe",
                    callbacks={
                        "pause_available": lambda _item, value: events.append(value),
                    },
                )

        self.assertTrue(result.success)
        self.assertEqual(events, [True, False, "verify"])

    def test_parse_crf_clamps_and_defaults(self):
        self.assertEqual(parse_crf("25"), 25)
        self.assertEqual(parse_crf("-4"), 0)
        self.assertEqual(parse_crf("80"), 51)
        self.assertEqual(parse_crf("bad"), 25)

    def test_parse_selection(self):
        self.assertEqual(parse_selection("", 4), [0, 1, 2, 3])
        self.assertEqual(parse_selection("y", 3), [0, 1, 2])
        self.assertIsNone(parse_selection("n", 3))
        self.assertEqual(parse_selection("2", 3), [1])
        self.assertEqual(parse_selection("1,3", 3), [0, 2])
        self.assertEqual(parse_selection("2-4", 5), [1, 2, 3])
        self.assertEqual(parse_selection("9", 3), [])

    def test_video_extension_detection(self):
        self.assertTrue(is_video_file("直拍 01.MOV"))
        self.assertTrue(is_video_file("stage.webm"))
        self.assertFalse(is_video_file("cover.jpg"))

    def test_scan_videos_file_and_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nested = root / "王某某 见面会"
            nested.mkdir()
            video = nested / "直拍 01.MP4"
            video.write_bytes(b"fake")
            (nested / "cover.jpg").write_bytes(b"fake")
            compressed = nested / "compressed"
            compressed.mkdir()
            (compressed / "直拍 01_compressed.mp4").write_bytes(b"fake")

            scanned = scan_videos(root)
            self.assertEqual([item.path for item in scanned], [video.resolve()])
            self.assertEqual(scan_videos(video)[0].path, video.resolve())

    def test_resolve_ffmpeg_does_not_return_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ffmpeg").mkdir()
            with patch("compressor_core.shutil.which", return_value=None):
                self.assertIsNone(resolve_ffmpeg_path(root))

    def test_output_path_conflict_policies(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "clip.mp4"
            video.write_bytes(b"fake")
            item = VideoItem(video)

            opts = CompressionOptions(conflict_policy="skip")
            self.assertEqual(build_output_path(item, opts), root / "compressed" / "clip_compressed.mp4")

            output_dir = root / "compressed"
            output_dir.mkdir()
            existing = output_dir / "clip_compressed.mp4"
            existing.write_bytes(b"old")
            rename_opts = CompressionOptions(conflict_policy="rename")
            self.assertEqual(build_output_path(item, rename_opts), output_dir / "clip_compressed_2.mp4")

    def test_build_ffmpeg_command_contains_expected_options(self):
        video = Path("input.mp4")
        output = Path("out.mp4")
        opts = CompressionOptions(crf=23, preset="medium", audio_bitrate="160k")
        cmd = build_ffmpeg_command("ffmpeg", video, output, opts)
        self.assertIn("-crf", cmd)
        self.assertIn("23", cmd)
        self.assertIn("-preset", cmd)
        self.assertIn("medium", cmd)
        self.assertIn("-b:a", cmd)
        self.assertIn("160k", cmd)
        self.assertIn("-xerror", cmd)
        self.assertEqual(cmd[-1], str(output))

    def test_v15_facade_exposes_new_version_and_unique_temp_paths(self):
        final_path = Path("out.mp4")
        first = temporary_output_path(final_path)
        second = temporary_output_path(final_path)

        self.assertEqual(APP_VERSION, "1.5.0")
        self.assertNotEqual(first, second)
        self.assertEqual(first.suffix, ".mp4")
        self.assertIn(str(__import__("os").getpid()), first.name)

    def test_profile_options_use_fixed_profile_or_custom_advanced_values(self):
        quality = CompressionOptions(
            profile_id="quality", crf=51, preset="ultrafast", audio_bitrate="64k", max_height=720
        )
        custom = CompressionOptions(
            profile_id="custom", crf=21, preset="slow", audio_bitrate="192k", max_height=720
        )

        quality_command = build_ffmpeg_command("ffmpeg", "input.mp4", "quality.mp4", quality)
        custom_command = build_ffmpeg_command("ffmpeg", "input.mp4", "custom.mp4", custom)

        self.assertEqual(quality.normalized().profile_id, "quality")
        self.assertEqual(quality.normalized().max_height, 720)
        self.assertEqual(quality_command[quality_command.index("-crf") + 1], "23")
        self.assertEqual(quality_command[quality_command.index("-preset") + 1], "medium")
        self.assertEqual(custom_command[custom_command.index("-crf") + 1], "21")
        self.assertIn("720", custom_command[custom_command.index("-vf") + 1])

    def test_explicit_share_profile_overrides_advanced_values_but_legacy_values_remain_custom(self):
        explicit_share = CompressionOptions(profile_id="share", crf=18, preset="slow", audio_bitrate="256k")
        legacy_manual = CompressionOptions(crf=18, preset="slow", audio_bitrate="256k")

        explicit_command = build_ffmpeg_command("ffmpeg", "input.mp4", "share.mp4", explicit_share)
        legacy_command = build_ffmpeg_command("ffmpeg", "input.mp4", "custom.mp4", legacy_manual)

        self.assertEqual(explicit_command[explicit_command.index("-crf") + 1], "25")
        self.assertEqual(legacy_command[legacy_command.index("-crf") + 1], "18")

    def test_compress_video_probes_builds_v15_command_and_verifies_before_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "input.mp4"
            source.write_bytes(b"source bytes")
            info = MediaInfo(path=source, duration_ms=1000, size_bytes=source.stat().st_size, has_audio=True)
            _WritingController.instances.clear()
            with (
                patch("compressor_core.probe_media", return_value=info) as probe,
                patch("compressor_core.ProcessController", _WritingController),
                patch(
                    "compressor_core.verify_output",
                    return_value=VerificationResult(True, MediaInfo(video_codec="h264", pixel_format="yuv420p", has_audio=True)),
                ) as verify,
            ):
                result = compress_video(source, CompressionOptions(profile_id="small"), ffmpeg="ffmpeg", ffprobe="ffprobe")

        self.assertTrue(result.success)
        self.assertEqual(result.state, JobState.SUCCEEDED)
        self.assertTrue(probe.called)
        self.assertTrue(verify.called)
        self.assertEqual(verify.call_args.kwargs["ffmpeg"], "ffmpeg")
        command = _WritingController.instances[0].command
        self.assertIn("1080", command[command.index("-vf") + 1])
        self.assertEqual(command[command.index("-map") + 1], "0:v:0")
        self.assertTrue(_WritingController.instances[0].process.stdout.closed)
        self.assertTrue(_WritingController.instances[0].process.stderr.closed)

    def test_compress_video_returns_structured_disk_space_failure_before_probe(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "input.mp4"
            source.write_bytes(b"source bytes")
            with (
                patch("compressor_core.shutil.disk_usage", return_value=(100, 0, 1)),
                patch("compressor_core.probe_media") as probe,
            ):
                result = compress_video(source, CompressionOptions(), ffmpeg="ffmpeg", ffprobe="ffprobe")

        self.assertEqual(result.state, JobState.FAILED)
        self.assertEqual(result.error_code, "insufficient_disk_space")
        self.assertFalse(probe.called)

    def test_cancel_requests_controller_even_when_stdout_is_silent(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "input.mp4"
            source.write_bytes(b"source bytes")
            token = CancelToken()
            _CancellingController.instances.clear()
            _CancellingController.cancel_on_start = token.cancel
            with (
                patch("compressor_core.probe_media", return_value=MediaInfo(path=source, duration_ms=1000)),
                patch("compressor_core.ProcessController", _CancellingController),
            ):
                result = compress_video(source, CompressionOptions(), cancel_token=token, ffmpeg="ffmpeg", ffprobe="ffprobe")

            _CancellingController.cancel_on_start = None

        self.assertTrue(result.cancelled)
        self.assertEqual(result.state, JobState.CANCELLING)
        self.assertTrue(_CancellingController.instances[0].cancel_requested)

    def test_cancel_waits_for_termination_instead_of_polling_forever(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "input.mp4"
            source.write_bytes(b"source bytes")
            token = CancelToken()
            results = []
            _WaitRequiredCancellationController.instances.clear()
            _WaitRequiredCancellationController.cancel_on_start = token.cancel

            def run_compression():
                with (
                    patch(
                        "compressor_core.probe_media",
                        return_value=MediaInfo(path=source, duration_ms=1000),
                    ),
                    patch(
                        "compressor_core.ProcessController",
                        _WaitRequiredCancellationController,
                    ),
                ):
                    results.append(
                        compress_video(
                            source,
                            CompressionOptions(),
                            cancel_token=token,
                            ffmpeg="ffmpeg",
                            ffprobe="ffprobe",
                        )
                    )

            worker = threading.Thread(target=run_compression, daemon=True)
            worker.start()
            worker.join(timeout=1.0)
            _WaitRequiredCancellationController.cancel_on_start = None

            self.assertFalse(worker.is_alive(), "cancelled compression polled forever")
            self.assertTrue(results[0].cancelled)
            self.assertGreaterEqual(
                _WaitRequiredCancellationController.instances[0].wait_calls,
                1,
            )

    def test_verification_failure_deletes_invalid_temp_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "input.mp4"
            source.write_bytes(b"source bytes")
            with (
                patch("compressor_core.probe_media", return_value=MediaInfo(path=source, duration_ms=1000)),
                patch("compressor_core.ProcessController", _WritingController),
                patch("compressor_core.verify_output", return_value=VerificationResult(False, error_code="duration_mismatch", diagnostic="bad duration")),
            ):
                result = compress_video(source, CompressionOptions(), ffmpeg="ffmpeg", ffprobe="ffprobe")

            self.assertEqual(result.error_code, "duration_mismatch")
            self.assertFalse(list((Path(tmp) / "compressed").glob("*.part.mp4")))

    def test_verification_cancellation_does_not_commit_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "input.mp4"
            source.write_bytes(b"source bytes")
            with (
                patch("compressor_core.probe_media", return_value=MediaInfo(path=source, duration_ms=1000)),
                patch("compressor_core.ProcessController", _WritingController),
                patch(
                    "compressor_core.verify_output",
                    return_value=VerificationResult(
                        False,
                        error_code="verification_cancelled",
                        diagnostic="cancelled",
                    ),
                ),
            ):
                result = compress_video(
                    source,
                    CompressionOptions(),
                    ffmpeg="ffmpeg",
                    ffprobe="ffprobe",
                )

            output_dir = Path(tmp) / "compressed"
            self.assertTrue(result.cancelled)
            self.assertEqual(result.state, JobState.CANCELLING)
            self.assertFalse(list(output_dir.glob("*.mp4")))

    def test_commit_failure_preserves_existing_output_and_valid_temp(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "input.mp4"
            source.write_bytes(b"source bytes")
            output_dir = Path(tmp) / "compressed"
            output_dir.mkdir()
            original = output_dir / "input_compressed.mp4"
            original.write_bytes(b"old output")
            with (
                patch("compressor_core.probe_media", return_value=MediaInfo(path=source, duration_ms=1000)),
                patch("compressor_core.ProcessController", _WritingController),
                patch("compressor_core.verify_output", return_value=VerificationResult(True)),
                patch("compressor_core.os.replace", side_effect=OSError("locked")),
            ):
                result = compress_video(source, CompressionOptions(conflict_policy="overwrite"), ffmpeg="ffmpeg", ffprobe="ffprobe")

            self.assertEqual(original.read_bytes(), b"old output")
            self.assertEqual(result.error_code, "commit_failed")
            self.assertIn(".part.mp4", result.diagnostic)
            self.assertTrue(list(output_dir.glob("*.part.mp4")))

    def test_batch_reserves_unique_custom_outputs_for_every_conflict_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first" / "clip.mp4"
            second = root / "second" / "clip.mp4"
            output_dir = root / "output"
            first.parent.mkdir()
            second.parent.mkdir()
            first.write_bytes(b"first source")
            second.write_bytes(b"second source")

            for policy in ("skip", "overwrite", "rename"):
                with self.subTest(policy=policy):
                    reserved = []

                    def capture(_video, _options, *_args, reserved_output_path=None, **_kwargs):
                        reserved.append(reserved_output_path)
                        return CompressionResult(success=True, output_path=reserved_output_path)

                    with patch("compressor_core.compress_video", side_effect=capture):
                        compress_batch(
                            [first, second],
                            CompressionOptions(
                                output_mode="custom",
                                output_dir=output_dir,
                                conflict_policy=policy,
                            ),
                        )

                    self.assertEqual(
                        reserved,
                        [output_dir / "clip_compressed.mp4", output_dir / "clip_compressed_2.mp4"],
                    )

    def test_batch_never_reserves_any_input_path_as_an_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output_dir = root / "shared"
            output_dir.mkdir()
            first = root / "source" / "clip.mp4"
            protected_input = output_dir / "clip_compressed.mp4"
            first.parent.mkdir()
            first.write_bytes(b"first source")
            protected_input.write_bytes(b"protected source")
            reserved = []

            def capture(_video, _options, *_args, reserved_output_path=None, **_kwargs):
                reserved.append(Path(reserved_output_path))
                return CompressionResult(success=True, output_path=reserved_output_path)

            with patch("compressor_core.compress_video", side_effect=capture):
                compress_batch(
                    [first, protected_input],
                    CompressionOptions(
                        output_mode="custom",
                        output_dir=output_dir,
                        conflict_policy="overwrite",
                    ),
                )

            self.assertNotIn(protected_input.resolve(), [path.resolve() for path in reserved])
            self.assertEqual(reserved[0], output_dir / "clip_compressed_2.mp4")

    def test_compress_video_rejects_a_reserved_output_equal_to_its_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "input.mp4"
            source.write_bytes(b"source bytes")

            result = compress_video(
                source,
                CompressionOptions(conflict_policy="overwrite"),
                reserved_output_path=source,
                ffmpeg="ffmpeg",
                ffprobe="ffprobe",
            )

            self.assertFalse(result.success)
            self.assertEqual(result.error_code, "unsafe_output_path")
            self.assertEqual(source.read_bytes(), b"source bytes")

    def test_batch_executes_unique_custom_outputs_for_every_conflict_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first" / "clip.mp4"
            second = root / "second" / "clip.mp4"
            first.parent.mkdir()
            second.parent.mkdir()
            first.write_bytes(b"first source")
            second.write_bytes(b"second source")

            for policy in ("skip", "overwrite", "rename"):
                with self.subTest(policy=policy):
                    output_dir = root / policy
                    _WritingController.instances.clear()
                    with (
                        patch(
                            "compressor_core.probe_media",
                            side_effect=lambda path, *_args, **_kwargs: MediaInfo(
                                path=Path(path), duration_ms=1000
                            ),
                        ),
                        patch("compressor_core.ProcessController", _WritingController),
                        patch("compressor_core.verify_output", return_value=VerificationResult(True)),
                    ):
                        summary = compress_batch(
                            [first, second],
                            CompressionOptions(
                                output_mode="custom",
                                output_dir=output_dir,
                                conflict_policy=policy,
                            ),
                            ffmpeg="ffmpeg",
                            ffprobe="ffprobe",
                        )

                    self.assertEqual(
                        [result.output_path for result in summary.results],
                        [output_dir / "clip_compressed.mp4", output_dir / "clip_compressed_2.mp4"],
                    )
                    self.assertTrue(all(result.output_path.exists() for result in summary.results))

    def test_skip_publish_does_not_overwrite_target_created_by_another_instance(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "input.mp4"
            source.write_bytes(b"source bytes")
            output = Path(tmp) / "compressed" / "input_compressed.mp4"

            def lose_publish_race(_temp, target):
                Path(target).write_bytes(b"other instance")
                raise FileExistsError(target)

            with (
                patch("compressor_core.probe_media", return_value=MediaInfo(path=source, duration_ms=1000)),
                patch("compressor_core.ProcessController", _WritingController),
                patch("compressor_core.verify_output", return_value=VerificationResult(True)),
                patch("compressor_core.os.link", side_effect=lose_publish_race),
            ):
                result = compress_video(
                    source,
                    CompressionOptions(conflict_policy="skip"),
                    ffmpeg="ffmpeg",
                    ffprobe="ffprobe",
                )

            self.assertTrue(result.skipped)
            self.assertEqual(output.read_bytes(), b"other instance")
            self.assertFalse(list(output.parent.glob("*.part.mp4")))

    def test_rename_publish_retries_after_another_instance_claims_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "input.mp4"
            source.write_bytes(b"source bytes")
            output_dir = Path(tmp) / "compressed"
            first_output = output_dir / "input_compressed.mp4"
            second_output = output_dir / "input_compressed_2.mp4"
            real_link = os.link
            attempts = []

            def race_once(temp, target):
                attempts.append(Path(target))
                if len(attempts) == 1:
                    Path(target).write_bytes(b"other instance")
                    raise FileExistsError(target)
                return real_link(temp, target)

            with (
                patch("compressor_core.probe_media", return_value=MediaInfo(path=source, duration_ms=1000)),
                patch("compressor_core.ProcessController", _WritingController),
                patch("compressor_core.verify_output", return_value=VerificationResult(True)),
                patch("compressor_core.os.link", side_effect=race_once),
            ):
                result = compress_video(
                    source,
                    CompressionOptions(conflict_policy="rename"),
                    ffmpeg="ffmpeg",
                    ffprobe="ffprobe",
                )

            self.assertTrue(result.success)
            self.assertEqual(attempts, [first_output, second_output])
            self.assertEqual(first_output.read_bytes(), b"other instance")
            self.assertEqual(second_output.read_bytes(), b"x")
            self.assertFalse(list(output_dir.glob("*.part.mp4")))

    def test_not_smaller_results_succeed_without_counting_as_saved_space(self):
        result = CompressionResult(
            success=True,
            original_size=10,
            output_size=20,
            state=JobState.NOT_SMALLER,
        )
        with patch("compressor_core.compress_video", return_value=result):
            summary = compress_batch(["input.mp4"], CompressionOptions())

        self.assertEqual(summary.not_smaller_count, 1)
        self.assertEqual(summary.success_count, 0)
        self.assertEqual(summary.saved_size, 0)

    def test_equal_size_output_is_classified_as_not_smaller(self):
        class EqualSizeController(_WritingController):
            def start(self, command, **kwargs):
                self.command = list(command)
                source = Path(command[command.index("-i") + 1])
                Path(command[-1]).write_bytes(source.read_bytes())
                return self.process

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "input.mp4"
            source.write_bytes(b"same size bytes")
            with (
                patch("compressor_core.probe_media", return_value=MediaInfo(path=source, duration_ms=1000)),
                patch("compressor_core.ProcessController", EqualSizeController),
                patch("compressor_core.verify_output", return_value=VerificationResult(True)),
            ):
                result = compress_video(
                    source,
                    CompressionOptions(),
                    ffmpeg="ffmpeg",
                    ffprobe="ffprobe",
                )

        self.assertTrue(result.success)
        self.assertEqual(result.output_size, result.original_size)
        self.assertEqual(result.state, JobState.NOT_SMALLER)

    def test_callback_exceptions_are_logged_and_do_not_escape_compression(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "input.mp4"
            source.write_bytes(b"source bytes")
            with (
                patch("compressor_core.probe_media", return_value=MediaInfo(path=source, duration_ms=1000)),
                patch("compressor_core.ProcessController", _WritingController),
                patch("compressor_core.verify_output", return_value=VerificationResult(True)),
            ):
                result = compress_video(
                    source,
                    CompressionOptions(),
                    callbacks={"status": lambda *_args: (_ for _ in ()).throw(RuntimeError("callback boom"))},
                    ffmpeg="ffmpeg",
                    ffprobe="ffprobe",
                )

        self.assertTrue(result.success)


if __name__ == "__main__":
    unittest.main()
