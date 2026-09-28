# -*- coding: utf-8 -*-

import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from compressor_core import CompressionOptions, compress_video, resolve_ffmpeg_path, resolve_ffprobe_path
from video_compressor.models import JobState
from video_compressor.probe import probe_media


class V15IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ffmpeg = resolve_ffmpeg_path()
        cls.ffprobe = resolve_ffprobe_path()
        if not cls.ffmpeg or not cls.ffprobe:
            raise unittest.SkipTest("ffmpeg/ffprobe are not available")

    def test_sdr_audio_compression_preserves_input_and_verifies_v15_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "中文 空格 😀"
            root.mkdir()
            source = root / "测试 输入.mp4"
            generated = subprocess.run(
                [
                    self.ffmpeg,
                    "-hide_banner",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc2=size=320x240:rate=24",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=1000:sample_rate=48000",
                    "-t",
                    "1",
                    "-c:v",
                    "mpeg4",
                    "-q:v",
                    "2",
                    "-c:a",
                    "aac",
                    str(source),
                ],
                capture_output=True,
                check=False,
            )
            if generated.returncode:
                self.skipTest(generated.stderr.decode("utf-8", errors="replace")[-400:])
            original_hash = hashlib.sha256(source.read_bytes()).hexdigest()

            result = compress_video(
                source,
                CompressionOptions(profile_id="share"),
                ffmpeg=self.ffmpeg,
                ffprobe=self.ffprobe,
            )

            self.assertTrue(result.success, result.diagnostic or result.error)
            self.assertIn(result.state, (JobState.SUCCEEDED, JobState.NOT_SMALLER))
            self.assertIsNotNone(result.input_info)
            self.assertIsNotNone(result.output_info)
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), original_hash)
            output = probe_media(result.output_path, self.ffprobe)
            self.assertEqual(output.video_codec, "h264")
            self.assertEqual(output.pixel_format, "yuv420p")
            self.assertTrue(output.has_audio)
            self.assertLess(abs(output.duration_ms - 1000), 1000)


if __name__ == "__main__":
    unittest.main()
