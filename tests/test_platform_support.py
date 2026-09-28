# -*- coding: utf-8 -*-

import tempfile
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from video_compressor.platform_support import (
    default_log_dir,
    remove_shortcut_texts,
    runtime_arch,
    start_shortcut_text,
    supports_process_pause,
    supports_sleep_inhibition,
)


class PlatformSupportTests(unittest.TestCase):
    def test_macos_defaults_are_native(self) -> None:
        home = Path("/Users/fan")

        self.assertEqual(
            default_log_dir("darwin", {}, home),
            home / "Library" / "Logs" / "VideoCompressor",
        )
        self.assertEqual(start_shortcut_text("darwin"), "Meta+Return")
        self.assertEqual(remove_shortcut_texts("darwin"), ("Backspace", "Delete"))

    def test_environment_log_override_wins_on_every_platform(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                default_log_dir("darwin", {"VIDEO_COMPRESSOR_LOG_DIR": tmp}),
                Path(tmp),
            )

    def test_windows_and_linux_defaults_remain_compatible(self) -> None:
        home = Path("/Users/fan")

        self.assertEqual(
            default_log_dir("win32", {"LOCALAPPDATA": "C:/Local"}, home),
            Path("C:/Local") / "VideoCompressor" / "logs",
        )
        self.assertEqual(
            default_log_dir("linux", {}, home),
            home / ".local" / "state" / "video-compressor",
        )
        self.assertEqual(start_shortcut_text("win32"), "Ctrl+Return")
        self.assertEqual(remove_shortcut_texts("win32"), ("Delete",))

    def test_arch_uses_platform_machine(self) -> None:
        with patch("video_compressor.platform_support.platform.machine", return_value="arm64"):
            self.assertEqual(runtime_arch(), "arm64")

    def test_arch_falls_back_to_windows_environment(self) -> None:
        with (
            patch("video_compressor.platform_support.platform.machine", return_value=""),
            patch.dict("video_compressor.platform_support.os.environ", {"PROCESSOR_ARCHITECTURE": "AMD64"}),
        ):
            self.assertEqual(runtime_arch(), "AMD64")

    def test_power_and_pause_supported_on_windows_and_macos(self) -> None:
        for platform_name in ("darwin", "win32"):
            self.assertTrue(supports_sleep_inhibition(platform_name))
            self.assertTrue(supports_process_pause(platform_name))
        self.assertFalse(supports_sleep_inhibition("linux"))
        self.assertFalse(supports_process_pause("linux"))


if __name__ == "__main__":
    unittest.main()
