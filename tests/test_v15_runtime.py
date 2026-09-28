# -*- coding: utf-8 -*-

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from video_compressor.models import JobState, MediaInfo
from video_compressor.probe import probe_media
from video_compressor import runner as runner_module
from video_compressor.runner import ProcessController, process_startup_kwargs
from video_compressor.verify import verify_output

ProcessRunResult = getattr(runner_module, "ProcessRunResult", None)
run_bounded_process = getattr(runner_module, "run_bounded_process", None)


class FakeProcess:
    def __init__(self, wait_times_out=False):
        self.pid = 4242
        self.returncode = None
        self.terminate_calls = 0
        self.kill_calls = 0
        self.wait_calls = 0
        self.wait_times_out = wait_times_out

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminate_calls += 1

    def kill(self):
        self.kill_calls += 1
        self.returncode = -9

    def wait(self, timeout=None):
        self.wait_calls += 1
        if self.wait_times_out:
            raise subprocess.TimeoutExpired("ffmpeg", timeout)
        if self.returncode is not None:
            return self.returncode
        self.returncode = 0
        return self.returncode


class CommunicatingProcess(FakeProcess):
    def __init__(self, cancel_token=None):
        super().__init__()
        self.stdout = tempfile.SpooledTemporaryFile()
        self.stderr = tempfile.SpooledTemporaryFile()
        self.cancel_token = cancel_token
        self.communicate_calls = 0

    def communicate(self, timeout=None):
        self.communicate_calls += 1
        if self.cancel_token is not None and self.returncode is None:
            self.cancel_token.cancel()
            raise subprocess.TimeoutExpired("ffprobe", timeout)
        self.returncode = 0 if self.returncode is None else self.returncode
        return b'{"streams": [], "format": {}}', b""

    def terminate(self):
        super().terminate()
        self.returncode = -15


class Token:
    def __init__(self):
        self.cancelled = False

    def cancel(self):
        self.cancelled = True

    def is_cancelled(self):
        return self.cancelled


class FakePsutilError(Exception):
    pass


class FakeNativeProcess:
    def __init__(self) -> None:
        self.actions: list[str] = []

    def suspend(self) -> None:
        self.actions.append("suspend")

    def resume(self) -> None:
        self.actions.append("resume")


class FakePsutil:
    Error = FakePsutilError

    def __init__(self, native: FakeNativeProcess) -> None:
        self.native = native
        self.pids: list[int] = []

    def Process(self, pid: int) -> FakeNativeProcess:
        self.pids.append(pid)
        return self.native


class ProcessControllerTests(unittest.TestCase):
    def test_pause_and_resume_signal_the_ffmpeg_process_group(self):
        process = FakeProcess()
        controller = ProcessController(
            popen_factory=lambda *_args, **_kwargs: process,
            platform_name="darwin",
        )
        controller.start(["ffmpeg", "-version"])

        with patch("video_compressor.runner.os.killpg", create=True) as killpg:
            self.assertTrue(controller.pause())
            self.assertTrue(controller.resume())

        self.assertEqual(
            killpg.call_args_list,
            [call(4242, runner_module.SIGSTOP), call(4242, runner_module.SIGCONT)],
        )

    def test_pause_and_resume_are_idempotent(self):
        process = FakeProcess()
        controller = ProcessController(
            popen_factory=lambda *_args, **_kwargs: process,
            platform_name="darwin",
        )
        controller.start(["ffmpeg", "-version"])

        with patch("video_compressor.runner.os.killpg", create=True) as killpg:
            self.assertTrue(controller.pause())
            self.assertTrue(controller.pause())
            self.assertTrue(controller.resume())
            self.assertTrue(controller.resume())

        self.assertEqual(
            killpg.call_args_list,
            [call(4242, runner_module.SIGSTOP), call(4242, runner_module.SIGCONT)],
        )

    def test_cancel_resumes_a_paused_process_before_terminating(self):
        events = []
        process = FakeProcess()
        process.terminate = lambda: events.append("terminate")
        controller = ProcessController(
            popen_factory=lambda *_args, **_kwargs: process,
            platform_name="darwin",
        )
        controller.start(["ffmpeg", "-version"])

        with patch(
            "video_compressor.runner.os.killpg",
            create=True,
            side_effect=lambda _pid, sent_signal: events.append(sent_signal),
        ):
            controller.pause()
            controller.request_cancel()

        self.assertEqual(
            events,
            [runner_module.SIGSTOP, runner_module.SIGCONT, "terminate"],
        )

    def test_pause_is_unavailable_on_unsupported_platform_or_after_exit(self):
        process = FakeProcess()
        controller = ProcessController(
            popen_factory=lambda *_args, **_kwargs: process,
            platform_name="linux",
        )
        controller.start(["ffmpeg", "-version"])
        self.assertFalse(controller.pause())
        process.returncode = 0
        controller._platform_name = "darwin"
        self.assertFalse(controller.pause())

    def test_windows_pause_resume_and_cancel_resume_first(self):
        native = FakeNativeProcess()
        module = FakePsutil(native)
        process = FakeProcess()
        controller = ProcessController(
            popen_factory=lambda *_args, **_kwargs: process,
            platform_name="win32",
            psutil_module=module,
        )
        controller.start(["ffmpeg", "-version"])

        self.assertTrue(controller.pause())
        self.assertTrue(controller.pause())
        self.assertTrue(controller.resume())
        self.assertTrue(controller.pause())
        self.assertTrue(controller.request_cancel())

        self.assertEqual(module.pids, [4242])
        self.assertEqual(native.actions, ["suspend", "resume", "suspend", "resume"])
        self.assertEqual(process.terminate_calls, 1)

    def test_request_cancel_terminates_without_waiting_on_streams(self):
        process = FakeProcess()
        controller = ProcessController(popen_factory=lambda *_args, **_kwargs: process)
        controller.start(["ffmpeg", "-version"])

        self.assertTrue(controller.request_cancel())
        self.assertEqual(process.terminate_calls, 1)
        self.assertEqual(process.wait_calls, 0)

    def test_wait_kills_process_after_cancel_grace_period(self):
        process = FakeProcess()
        now = [0.0]
        controller = ProcessController(
            popen_factory=lambda *_args, **_kwargs: process,
            clock=lambda: now[0],
        )
        controller.start(["ffmpeg", "-version"])
        controller.request_cancel()
        now[0] = 4.0

        self.assertEqual(controller.wait(), -9)
        self.assertEqual(process.kill_calls, 1)
        self.assertEqual(process.wait_calls, 1)

    def test_windows_process_flags_hide_console_window(self):
        with patch("video_compressor.runner.os.name", "nt"), patch.object(
            subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True
        ):
            kwargs = process_startup_kwargs()

        self.assertEqual(kwargs["creationflags"], 0x08000000)
        self.assertNotIn("start_new_session", kwargs)

    def test_bounded_process_closes_stdout_and_stderr(self):
        process = CommunicatingProcess()
        controller = ProcessController(popen_factory=lambda *_args, **_kwargs: process)

        result = run_bounded_process(["ffprobe", "input.mp4"], controller=controller)

        self.assertEqual(result.returncode, 0)
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stderr.closed)

    def test_bounded_process_honors_cancel_token_during_communicate(self):
        token = Token()
        process = CommunicatingProcess(token)
        controller = ProcessController(popen_factory=lambda *_args, **_kwargs: process)

        result = run_bounded_process(
            ["ffprobe", "input.mp4"],
            cancel_token=token,
            controller=controller,
        )

        self.assertTrue(result.cancelled)
        self.assertEqual(process.terminate_calls, 1)
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stderr.closed)

    def test_bounded_process_times_out_and_terminates_process(self):
        process = CommunicatingProcess(Token())
        controller = ProcessController(popen_factory=lambda *_args, **_kwargs: process)

        result = run_bounded_process(
            ["ffprobe", "input.mp4"],
            timeout=0.001,
            controller=controller,
        )

        self.assertTrue(result.timed_out)
        self.assertEqual(process.terminate_calls, 1)


class ProbeMediaTests(unittest.TestCase):
    def test_probe_media_uses_bounded_process_and_reports_cancellation(self):
        token = Token()
        token.cancel()
        run = Mock(return_value=ProcessRunResult(cancelled=True))

        with patch("video_compressor.probe.run_bounded_process", run):
            result = probe_media("input.mp4", "ffprobe", cancel_token=token, timeout=2)

        self.assertEqual(result.state, JobState.CANCELLING)
        self.assertEqual(result.error_code, "probe_cancelled")

    def test_probe_media_passes_timeout_to_bounded_process(self):
        run = Mock(
            return_value=ProcessRunResult(
                returncode=0,
                stdout=b'{"streams": [], "format": {}}',
            )
        )

        with patch("video_compressor.probe.run_bounded_process", run):
            result = probe_media("input.mp4", "ffprobe", timeout=2)

        self.assertFalse(result.error_code)
        self.assertEqual(run.call_args.kwargs["timeout"], 2)


class VerifyOutputTests(unittest.TestCase):
    def test_verify_output_accepts_h264_sdr_with_expected_audio_and_duration(self):
        source = MediaInfo(duration_ms=10_000, has_audio=True)
        output = MediaInfo(
            duration_ms=10_050,
            video_codec="h264",
            pixel_format="yuv420p",
            has_audio=True,
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "output.mp4"
            path.write_bytes(b"encoded")
            with (
                patch("video_compressor.verify.probe_media", return_value=output),
                patch(
                    "video_compressor.verify.run_bounded_process",
                    return_value=ProcessRunResult(returncode=0),
                ) as decode,
            ):
                result = verify_output(source, path, "ffprobe", ffmpeg="ffmpeg")

        self.assertTrue(result.valid)
        self.assertEqual(result.output_info, output)
        self.assertEqual(result.error_code, "")
        command = decode.call_args.args[0]
        self.assertIn("-xerror", command)
        self.assertEqual(command[command.index("-f") + 1], "null")
        self.assertEqual(command[-1], "-")

    def test_verify_output_rejects_unexpected_video_codec(self):
        source = MediaInfo(duration_ms=10_000, has_audio=False)
        output = MediaInfo(duration_ms=10_000, video_codec="hevc", pixel_format="yuv420p")

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "output.mp4"
            path.write_bytes(b"encoded")
            with patch("video_compressor.verify.probe_media", return_value=output):
                result = verify_output(source, path, "ffprobe", ffmpeg="ffmpeg")

        self.assertFalse(result.valid)
        self.assertEqual(result.error_code, "unexpected_video_codec")

    def test_verify_output_rejects_full_decode_failure(self):
        source = MediaInfo(duration_ms=1000)
        output = MediaInfo(duration_ms=1000, video_codec="h264", pixel_format="yuv420p")

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "output.mp4"
            path.write_bytes(b"encoded")
            with (
                patch("video_compressor.verify.probe_media", return_value=output),
                patch(
                    "video_compressor.verify.run_bounded_process",
                    return_value=ProcessRunResult(returncode=1, stderr=b"decode error"),
                ),
            ):
                result = verify_output(source, path, "ffprobe", ffmpeg="ffmpeg")

        self.assertFalse(result.valid)
        self.assertEqual(result.error_code, "output_decode_failed")
        self.assertIn("decode error", result.diagnostic)

    def test_verify_output_reports_decode_cancellation(self):
        source = MediaInfo(duration_ms=1000)
        output = MediaInfo(duration_ms=1000, video_codec="h264", pixel_format="yuv420p")
        token = Token()

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "output.mp4"
            path.write_bytes(b"encoded")
            with (
                patch("video_compressor.verify.probe_media", return_value=output),
                patch(
                    "video_compressor.verify.run_bounded_process",
                    return_value=ProcessRunResult(cancelled=True),
                ),
            ):
                result = verify_output(
                    source,
                    path,
                    "ffprobe",
                    ffmpeg="ffmpeg",
                    cancel_token=token,
                )

        self.assertFalse(result.valid)
        self.assertEqual(result.error_code, "verification_cancelled")


if __name__ == "__main__":
    unittest.main()
