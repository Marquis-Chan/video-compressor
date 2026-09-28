# -*- coding: utf-8 -*-

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from video_compressor.runner import ProcessController


ROOT = Path(__file__).resolve().parents[1]
FFMPEG = ROOT / "ffmpeg" / "windows-x64" / "bin" / "ffmpeg.exe"
if not FFMPEG.is_file():
    FFMPEG = ROOT / "ffmpeg" / "bin" / "ffmpeg.exe"


@unittest.skipUnless(sys.platform == "win32", "requires Windows")
@unittest.skipUnless(
    os.environ.get("VIDEO_COMPRESSOR_REAL_WINDOWS_TEST") == "1",
    "set VIDEO_COMPRESSOR_REAL_WINDOWS_TEST=1 to run real FFmpeg process tests",
)
class WindowsProcessIntegrationTests(unittest.TestCase):
    def command(self, output: Path, duration: int) -> list[str]:
        return [
            str(FFMPEG),
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-re",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=30",
            "-t",
            str(duration),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
            "-y",
            str(output),
        ]

    def test_real_ffmpeg_pause_resume_and_cancel_while_paused(self) -> None:
        self.assertTrue(FFMPEG.is_file(), FFMPEG)
        with tempfile.TemporaryDirectory(prefix="video-compressor-process-") as folder:
            completed_output = Path(folder) / "completed.mp4"
            controller = ProcessController()
            controller.start(
                self.command(completed_output, 3),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(0.5)
            self.assertTrue(controller.pause())
            time.sleep(0.4)
            self.assertTrue(controller.resume())
            self.assertEqual(controller.wait(timeout=10), 0)
            self.assertGreater(completed_output.stat().st_size, 0)

            cancelled_output = Path(folder) / "cancelled.mp4"
            controller = ProcessController()
            controller.start(
                self.command(cancelled_output, 20),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(0.5)
            self.assertTrue(controller.pause())
            self.assertTrue(controller.request_cancel())
            self.assertIsNotNone(controller.wait())


if __name__ == "__main__":
    unittest.main()
