# -*- coding: utf-8 -*-

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from video_compressor.power import (
    ES_CONTINUOUS,
    ES_SYSTEM_REQUIRED,
    WindowsSleepInhibitor,
    create_sleep_inhibitor,
)


class WindowsSleepInhibitorTests(unittest.TestCase):
    def test_start_and_stop_use_system_required_without_display_required(self) -> None:
        calls: list[int] = []
        inhibitor = WindowsSleepInhibitor(
            platform_name="win32",
            execution_state=lambda flags: calls.append(flags) or flags,
        )

        self.assertTrue(inhibitor.start())
        self.assertTrue(inhibitor.start())
        inhibitor.stop()
        inhibitor.stop()

        self.assertEqual(calls, [ES_CONTINUOUS | ES_SYSTEM_REQUIRED, ES_CONTINUOUS])
        self.assertFalse(inhibitor.active)

    def test_failure_is_non_fatal(self) -> None:
        inhibitor = WindowsSleepInhibitor(
            platform_name="win32",
            execution_state=lambda _flags: 0,
        )

        self.assertFalse(inhibitor.start())
        inhibitor.stop()

    def test_factory_selects_windows_backend(self) -> None:
        inhibitor = create_sleep_inhibitor(enabled=True, platform_name="win32")
        self.assertIsInstance(inhibitor, WindowsSleepInhibitor)


if __name__ == "__main__":
    unittest.main()
