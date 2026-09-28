# -*- coding: utf-8 -*-

import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from video_compressor.power import MacSleepInhibitor


class FakeProcess:
    def __init__(self, *, time_out_on_first_wait: bool = False) -> None:
        self.returncode = None
        self.terminate_calls = 0
        self.kill_calls = 0
        self.wait_calls = 0
        self.time_out_on_first_wait = time_out_on_first_wait

    def poll(self):  # type: ignore[no-untyped-def]
        return self.returncode

    def terminate(self) -> None:
        self.terminate_calls += 1

    def kill(self) -> None:
        self.kill_calls += 1
        self.returncode = -9

    def wait(self, timeout=None):  # type: ignore[no-untyped-def]
        self.wait_calls += 1
        if self.time_out_on_first_wait and self.wait_calls == 1:
            raise subprocess.TimeoutExpired("caffeinate", timeout)
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


class MacSleepInhibitorTests(unittest.TestCase):
    def test_start_uses_caffeinate_idle_assertion_once(self) -> None:
        process = FakeProcess()
        popen = Mock(return_value=process)
        inhibitor = MacSleepInhibitor(platform_name="darwin", popen_factory=popen)

        self.assertTrue(inhibitor.start())
        self.assertTrue(inhibitor.start())

        popen.assert_called_once_with(
            ["/usr/bin/caffeinate", "-i"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        self.assertTrue(inhibitor.active)

    def test_stop_is_idempotent_and_reaps_process(self) -> None:
        process = FakeProcess()
        inhibitor = MacSleepInhibitor(
            platform_name="darwin",
            popen_factory=Mock(return_value=process),
        )
        inhibitor.start()

        inhibitor.stop()
        inhibitor.stop()

        self.assertEqual(process.terminate_calls, 1)
        self.assertEqual(process.wait_calls, 1)
        self.assertFalse(inhibitor.active)

    def test_stop_kills_caffeinate_after_bounded_grace_period(self) -> None:
        process = FakeProcess(time_out_on_first_wait=True)
        inhibitor = MacSleepInhibitor(
            platform_name="darwin",
            popen_factory=Mock(return_value=process),
        )
        inhibitor.start()

        inhibitor.stop()

        self.assertEqual(process.terminate_calls, 1)
        self.assertEqual(process.kill_calls, 1)
        self.assertEqual(process.wait_calls, 2)
        self.assertFalse(inhibitor.active)

    def test_disabled_non_macos_and_start_failure_are_safe(self) -> None:
        disabled_popen = Mock()
        non_macos_popen = Mock()
        failed_popen = Mock(side_effect=OSError("missing"))
        disabled = MacSleepInhibitor(
            enabled=False,
            platform_name="darwin",
            popen_factory=disabled_popen,
        )
        non_macos = MacSleepInhibitor(
            platform_name="win32",
            popen_factory=non_macos_popen,
        )
        failed = MacSleepInhibitor(
            platform_name="darwin",
            popen_factory=failed_popen,
        )

        self.assertFalse(disabled.start())
        self.assertFalse(non_macos.start())
        self.assertFalse(failed.start())
        disabled.stop()
        non_macos.stop()
        failed.stop()

        disabled_popen.assert_not_called()
        non_macos_popen.assert_not_called()
        failed_popen.assert_called_once()


if __name__ == "__main__":
    unittest.main()
