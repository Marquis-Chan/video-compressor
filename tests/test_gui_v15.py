# -*- coding: utf-8 -*-

import json
import os
import tempfile
import threading
import time
import unittest
from dataclasses import dataclass, field
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QSettings, Qt, QUrl
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication, QMessageBox

from compressor_core import CompressionOptions, JobState, ToolchainReport, VideoItem
from video_compress_gui import CompressionWorker, MainWindow, QueueDropPanel, ScanWorker
from video_compressor.platform_support import remove_shortcut_texts, start_shortcut_text


@dataclass
class FakeResult:
    success: bool = False
    skipped: bool = False
    cancelled: bool = False
    output_path: Path | None = None
    error: str = ""
    diagnostic: str = ""
    error_code: str = ""
    state: JobState = JobState.SUCCEEDED
    original_size: int = 0
    output_size: int = 0
    input_info: object | None = None


@dataclass
class FakeSummary:
    success_count: int = 0
    fail_count: int = 0
    skipped_count: int = 0
    not_smaller_count: int = 0
    cancelled: bool = False
    saved_size: int = 0
    results: list[FakeResult] = field(default_factory=list)


class GuiV15Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.settings_dir = tempfile.TemporaryDirectory()
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, cls.settings_dir.name)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.settings_dir.cleanup()

    def make_window(self) -> MainWindow:
        window = MainWindow(toolchain_validator=lambda **_kwargs: self.ready_toolchain())
        window.background.set_background("", 62, "cover", 0)
        self.addCleanup(window.close)
        self.wait_for(lambda: window._toolchain_report is not None)
        return window

    def wait_for(self, predicate, timeout: float = 2.0) -> None:  # type: ignore[no-untyped-def]
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            QApplication.processEvents()
            time.sleep(0.01)
        QApplication.processEvents()
        self.assertTrue(predicate())

    def add_items(self, window: MainWindow, count: int = 2) -> None:
        window.items = [
            VideoItem(Path(f"C:/fan-videos/event-{index}/clip-{index}.mp4"), size=100_000_000)
            for index in range(count)
        ]
        for item in window.items:
            item.output_path = window.preview_output_path(item)
        window.render_table()

    def ready_toolchain(self) -> ToolchainReport:
        return ToolchainReport(
            ready=True,
            platform=sys.platform,
            version="ffmpeg test",
            ffmpeg_path="ffmpeg",
            ffprobe_path="ffprobe",
            ffmpeg_available=True,
            ffprobe_available=True,
            libx264=True,
            aac=True,
            zscale=True,
            tonemap=True,
        )

    def test_toolchain_validator_is_injectable_and_cached_for_startup_and_start(self) -> None:
        validator = Mock(return_value=self.ready_toolchain())
        window = MainWindow(toolchain_validator=validator)
        self.addCleanup(window.close)
        self.wait_for(lambda: window._toolchain_report is not None)
        self.add_items(window, 1)

        with patch.object(CompressionWorker, "start"):
            window.start_compression()

        validator.assert_called_once()
        self.assertIn("已就绪", window.ffmpeg_label.text())

    def test_slow_toolchain_validation_does_not_block_construction_and_updates_when_done(self) -> None:
        started = threading.Event()
        release = threading.Event()

        def slow_validator(**_kwargs):  # type: ignore[no-untyped-def]
            started.set()
            release.wait(5)
            return self.ready_toolchain()

        before = time.monotonic()
        window = MainWindow(toolchain_validator=slow_validator)
        elapsed = time.monotonic() - before
        self.addCleanup(release.set)
        self.addCleanup(window.close)
        self.wait_for(started.is_set)
        self.add_items(window, 1)

        self.assertLess(elapsed, 0.5)
        self.assertEqual(window.ffmpeg_label.text(), "工具链检测中")
        self.assertFalse(window.start_btn.isEnabled())

        release.set()
        self.wait_for(lambda: window._toolchain_report is not None)

        self.assertIn("已就绪", window.ffmpeg_label.text())
        self.assertTrue(window.start_btn.isEnabled())

    def test_start_while_toolchain_is_still_checking_only_shows_wait_message(self) -> None:
        started = threading.Event()
        release = threading.Event()
        validator = Mock()

        def slow_validator(**_kwargs):  # type: ignore[no-untyped-def]
            started.set()
            release.wait(5)
            return self.ready_toolchain()

        validator.side_effect = slow_validator
        window = MainWindow(toolchain_validator=validator)
        self.addCleanup(release.set)
        self.addCleanup(window.close)
        self.wait_for(started.is_set)
        self.add_items(window, 1)

        with (
            patch.object(QMessageBox, "information") as information,
            patch.object(CompressionWorker, "start") as start,
        ):
            window.start_compression()

        information.assert_called_once()
        self.assertIn("检测", information.call_args.args[2])
        start.assert_not_called()
        validator.assert_called_once()
        release.set()
        self.wait_for(lambda: window._toolchain_report is not None)

    def test_incomplete_toolchain_disables_start_and_reports_every_missing_capability(self) -> None:
        report = ToolchainReport(
            ffmpeg_path="ffmpeg",
            ffmpeg_available=True,
            ffprobe_available=False,
            libx264=False,
            aac=False,
            zscale=False,
            tonemap=False,
            diagnostic="capability check failed",
        )
        window = MainWindow(toolchain_validator=lambda **_kwargs: report)
        self.addCleanup(window.close)
        self.wait_for(lambda: window._toolchain_report is not None)
        self.add_items(window, 1)

        self.assertFalse(window.start_btn.isEnabled())
        for capability in ("FFprobe", "libx264", "AAC", "zscale", "tonemap"):
            self.assertIn(capability, window.ffmpeg_label.toolTip())

        with patch.object(QMessageBox, "warning") as warning:
            window.start_compression()

        warning.assert_called_once()
        self.assertIsNone(window.worker)

    def test_start_is_disabled_for_empty_queue(self) -> None:
        window = self.make_window()
        self.assertFalse(window.start_btn.isEnabled())

    def test_remove_without_highlight_does_not_delete_checked_items(self) -> None:
        window = self.make_window()
        self.add_items(window)
        window.table.clearSelection()

        window.remove_selected_rows()

        self.assertEqual(len(window.items), 2)

    def test_minimum_window_size_supports_small_high_dpi_screens(self) -> None:
        window = self.make_window()
        self.assertLessEqual(window.minimumWidth(), 760)
        self.assertLessEqual(window.minimumHeight(), 520)

    def test_retry_failed_items_selects_only_failures_and_starts_compression(self) -> None:
        window = self.make_window()
        self.add_items(window)
        window.items[0].status = "失败"
        window.items[0].selected = False
        window.items[1].status = "完成"
        window.items[1].selected = True
        window.render_table()

        with patch.object(window, "start_compression") as start_compression:
            window.retry_failed_items()

        self.assertTrue(window.items[0].selected)
        self.assertEqual(window.items[0].status, "待压缩")
        self.assertFalse(window.items[1].selected)
        start_compression.assert_called_once_with()

    def test_v15_presets_and_advanced_changes_become_custom(self) -> None:
        window = self.make_window()
        labels = [button.text() for button in window.preset_buttons.buttons()]
        self.assertEqual(labels, ["高清分享", "清晰优先", "体积优先"])

        window.crf_spin.setValue(window.crf_spin.value() + 1)

        self.assertEqual(window.profile_label.text(), "自定义")
        self.assertEqual(window.profile_id, "custom")

    def test_profile_id_is_saved_and_small_profile_caps_height_at_1080p(self) -> None:
        window = self.make_window()
        small = next(button for button in window.preset_buttons.buttons() if button.text() == "体积优先")
        small.click()

        options = window.current_options()

        self.assertEqual(window.profile_id, "small")
        self.assertEqual(window.settings.value("compression/profile_id"), "small")
        self.assertEqual(options.crf, 28)
        self.assertEqual(options.max_height, 1080)

    def test_output_mode_and_directory_are_mapped_to_core_options(self) -> None:
        window = self.make_window()
        window.output_mode_combo.setCurrentIndex(window.output_mode_combo.findData("custom"))
        window.output_dir_edit.setText("C:/fan-output")

        options = window.current_options()

        self.assertEqual(options.output_mode, "custom")
        self.assertEqual(options.output_dir, Path("C:/fan-output"))

    def test_output_location_stays_visible_and_typing_switches_to_custom(self) -> None:
        window = self.make_window()
        window.show()
        window.advanced_toggle.setChecked(False)
        window.output_mode_combo.setCurrentIndex(
            window.output_mode_combo.findData("source_subfolder")
        )
        QApplication.processEvents()

        self.assertTrue(window.output_group.isVisibleTo(window.settings_scroll))
        self.assertFalse(window.advanced.isVisibleTo(window.settings_scroll))

        window.output_dir_edit.setText("C:/fan-output")
        window.output_dir_edit.textEdited.emit("C:/fan-output")
        QApplication.processEvents()

        self.assertEqual(window.output_mode_combo.currentData(), "custom")
        self.assertEqual(window.settings.value("output/mode"), "custom")
        self.assertEqual(window.settings.value("output/directory"), "C:/fan-output")

    def test_glass_surfaces_reveal_more_of_the_background(self) -> None:
        window = self.make_window()
        stylesheet = window.styleSheet()

        self.assertIn("rgba(248, 250, 252, 0.80)", stylesheet)
        self.assertIn("rgba(255, 255, 255, 0.84)", stylesheet)

    def test_bottom_progress_is_vertically_aligned_with_text_and_buttons(self) -> None:
        window = self.make_window()
        window.resize(1120, 720)
        window.show()
        QApplication.processEvents()

        bar_center = window.bottom_bar.rect().center().y()
        controls = (
            window.summary_label,
            window.total_progress,
            window.cancel_btn,
            window.start_btn,
        )
        centers = [
            control.mapTo(window.bottom_bar, control.rect().center()).y()
            for control in controls
        ]

        self.assertEqual(window.total_progress.height(), 18)
        self.assertTrue(all(abs(center - bar_center) <= 2 for center in centers), centers)

    def test_remove_button_tracks_highlight_selection(self) -> None:
        window = self.make_window()
        self.add_items(window)
        window.table.clearSelection()
        QApplication.processEvents()
        self.assertFalse(window.remove_btn.isEnabled())

        window.table.selectRow(0)
        QApplication.processEvents()
        self.assertTrue(window.remove_btn.isEnabled())

    def test_file_cell_tooltip_contains_parent_input_and_output_paths(self) -> None:
        window = self.make_window()
        self.add_items(window, 1)

        file_cell = window.table.item(0, 1)

        self.assertEqual(file_cell.text().splitlines()[0], "clip-0.mp4")
        self.assertIn("event-0", file_cell.text().splitlines()[1])
        self.assertIn("C:/fan-videos/event-0", file_cell.toolTip().replace("\\", "/"))
        self.assertIn(str(window.items[0].path), file_cell.toolTip())
        self.assertIn(str(window.items[0].output_path), file_cell.toolTip())
        self.assertIn("clip-0.mp4", file_cell.data(Qt.AccessibleTextRole))
        self.assertIn("event-0", file_cell.data(Qt.AccessibleDescriptionRole))

    def test_continuous_progress_includes_current_file_fraction(self) -> None:
        window = self.make_window()
        self.add_items(window, 4)
        window.active_batch_rows = [0, 1, 2, 3]
        window.completed_in_batch = 1

        window.on_item_progress(1, 50)

        self.assertEqual(window.total_progress.value(), 37)
        self.assertIn("clip-1.mp4", window.summary_label.text())

    def test_cancelled_batch_marks_remaining_items_not_executed(self) -> None:
        window = self.make_window()
        self.add_items(window, 3)
        for item in window.items:
            item.status = "等待中"
        window.active_batch_rows = [0, 1, 2]
        window.items[0].status = "完成"

        window.on_finished(FakeSummary(success_count=1, cancelled=True))

        self.assertEqual([item.status for item in window.items], ["完成", "未执行", "未执行"])
        self.assertEqual(window.result_bar.result_kind, "cancelled")
        self.assertFalse(window.result_bar.isHidden())

    def test_cancel_requested_prevents_callbacks_from_overwriting_cancelling_state(self) -> None:
        window = self.make_window()
        self.add_items(window, 1)
        window.active_batch_rows = [0]
        window.current_batch_row = 0
        window.items[0].status = "压缩中"
        worker = Mock()
        worker.isRunning.return_value = True
        window.worker = worker
        self.addCleanup(setattr, window, "worker", None)

        window.cancel_compression()
        window.on_item_progress(0, 37)
        window.on_item_status(0, "压缩中", "late status")
        window.on_batch_progress(0, 1, 0, 0, 0)

        self.assertTrue(window.cancel_requested)
        self.assertEqual(window.items[0].status, "正在取消")
        self.assertIn("正在取消", window.summary_label.text())
        worker.cancel.assert_called_once_with()
        window.worker = None

    def test_cancelled_scan_keeps_cancelling_label_and_finishes_as_cancelled(self) -> None:
        window = self.make_window()
        worker = Mock()
        worker.isRunning.return_value = True
        window.scan_worker = worker
        self.addCleanup(setattr, window, "scan_worker", None)

        window.cancel_scan()
        window.on_scan_progress(9)
        self.assertEqual(window.scan_label.text(), "正在取消扫描...")

        window.on_scan_finished([], 0)

        self.assertEqual(window.summary_label.text(), "扫描已取消")
        worker.cancel.assert_called_once_with()
        window.scan_worker = None

    def test_result_bar_distinguishes_partial_failure_and_all_skipped(self) -> None:
        window = self.make_window()
        self.add_items(window, 2)

        with patch.object(QMessageBox, "exec", return_value=QMessageBox.Rejected):
            window.on_finished(FakeSummary(success_count=1, fail_count=1))
        self.assertEqual(window.result_bar.result_kind, "partial")

        with patch.object(QMessageBox, "exec", return_value=QMessageBox.Rejected):
            window.on_finished(FakeSummary(skipped_count=2))
        self.assertEqual(window.result_bar.result_kind, "all_skipped")

    def test_not_smaller_output_is_presented_as_a_kept_result(self) -> None:
        window = self.make_window()
        self.add_items(window, 1)
        summary = FakeSummary(
            not_smaller_count=1,
            results=[
                FakeResult(
                    success=True,
                    state=JobState.NOT_SMALLER,
                    original_size=10_000_000,
                    output_size=12_000_000,
                    output_path=Path("C:/fan-videos/event-0/compressed/clip-0_compressed.mp4"),
                )
            ],
        )

        window.on_finished(summary)

        self.assertEqual(window.result_bar.result_kind, "not_smaller")
        self.assertIn("未变小 1", window.result_bar.detail_label.text())
        self.assertNotIn("压缩失败", window.result_bar.title_label.text())

    def test_only_failures_filter_hides_non_failed_rows(self) -> None:
        window = self.make_window()
        self.add_items(window, 2)
        window.items[0].status = "失败"
        window.items[1].status = "完成"
        window.render_table()

        window.show_failed_items()

        self.assertFalse(window.table.isRowHidden(0))
        self.assertTrue(window.table.isRowHidden(1))

    def test_open_directories_opens_only_default_directory(self) -> None:
        window = self.make_window()
        first = Path(self.settings_dir.name)
        second = first / "another"
        second.mkdir()

        with patch("video_compress_gui.QDesktopServices.openUrl") as open_url:
            window.open_directories([first, second])

        open_url.assert_called_once()

    def test_scanner_and_compressor_emit_completion_from_finally(self) -> None:
        scan_events: list[str] = []
        scan_worker = ScanWorker(["C:/broken"], set())
        scan_worker.workComplete.connect(lambda: scan_events.append("complete"))
        with patch("video_compress_gui.iter_video_paths", side_effect=RuntimeError("scan boom")):
            scan_worker.run()
        self.assertEqual(scan_events, ["complete"])

        compression_events: list[str] = []
        worker = CompressionWorker([], CompressionOptions())
        worker.workComplete.connect(lambda: compression_events.append("complete"))
        with patch("video_compress_gui.compress_batch", side_effect=RuntimeError("compress boom")):
            worker.run()
        self.assertEqual(compression_events, ["complete"])

    def test_worker_stops_sleep_inhibitor_while_paused_and_always_cleans_up(self) -> None:
        inhibitor = Mock()
        worker = CompressionWorker([], CompressionOptions(), prevent_sleep=True)
        states: list[bool] = []
        worker.pauseChanged.connect(states.append)

        def fake_batch(*_args, **kwargs):  # type: ignore[no-untyped-def]
            callbacks = kwargs["callbacks"]
            item = VideoItem(Path("clip.mp4"))
            callbacks["pause_changed"](item, True)
            callbacks["pause_changed"](item, False)
            return FakeSummary()

        with (
            patch("video_compress_gui.create_sleep_inhibitor", return_value=inhibitor),
            patch("video_compress_gui.compress_batch", side_effect=fake_batch),
        ):
            worker.run()

        self.assertEqual(
            inhibitor.method_calls,
            [call.start(), call.stop(), call.start(), call.stop()],
        )
        self.assertEqual(states, [True, False, False])

    def test_worker_does_not_reacquire_sleep_inhibitor_after_cancel(self) -> None:
        inhibitor = Mock()
        worker = CompressionWorker([], CompressionOptions(), prevent_sleep=True)

        def fake_batch(*_args, **kwargs):  # type: ignore[no-untyped-def]
            callbacks = kwargs["callbacks"]
            item = VideoItem(Path("clip.mp4"))
            callbacks["pause_changed"](item, True)
            worker.cancel()
            callbacks["pause_changed"](item, False)
            return FakeSummary(cancelled=True)

        with (
            patch("video_compress_gui.create_sleep_inhibitor", return_value=inhibitor),
            patch("video_compress_gui.compress_batch", side_effect=fake_batch),
        ):
            worker.run()

        self.assertEqual(
            inhibitor.method_calls,
            [call.start(), call.stop(), call.stop()],
        )

    def test_worker_pause_and_resume_only_update_the_thread_safe_token(self) -> None:
        worker = CompressionWorker([], CompressionOptions())

        worker.pause()

        self.assertTrue(worker.cancel_token.is_pause_requested())

        worker.resume()

        self.assertFalse(worker.cancel_token.is_pause_requested())

    def test_worker_clears_a_stale_pause_request_when_encoding_ends(self) -> None:
        worker = CompressionWorker([], CompressionOptions())

        def fake_batch(*_args, **kwargs):  # type: ignore[no-untyped-def]
            worker.pause()
            callbacks = kwargs["callbacks"]
            callbacks["pause_available"](VideoItem(Path("clip.mp4")), False)
            return FakeSummary()

        with patch("video_compress_gui.compress_batch", side_effect=fake_batch):
            worker.run()

        self.assertFalse(worker.cancel_token.is_pause_requested())

    def test_scanner_and_compressor_exceptions_emit_structured_diagnostics(self) -> None:
        scan_errors: list[dict] = []
        scan_worker = ScanWorker(["C:/broken"], set())
        scan_worker.workerError.connect(scan_errors.append)
        with patch("video_compress_gui.iter_video_paths", side_effect=RuntimeError("scan boom")):
            scan_worker.run()

        compression_errors: list[dict] = []
        worker = CompressionWorker([], CompressionOptions())
        worker.workerError.connect(compression_errors.append)
        with patch("video_compress_gui.compress_batch", side_effect=RuntimeError("compress boom")):
            worker.run()

        self.assertEqual(scan_errors[0]["error_code"], "scan_worker_exception")
        self.assertIn("scan boom", scan_errors[0]["summary"])
        self.assertIn("traceback", scan_errors[0])
        self.assertEqual(scan_errors[0].get("input_path"), "")
        self.assertEqual(scan_errors[0].get("output_path"), "")
        self.assertEqual(compression_errors[0]["error_code"], "compression_worker_exception")
        self.assertIn("compress boom", compression_errors[0]["summary"])
        self.assertEqual(compression_errors[0].get("input_path"), "")
        self.assertEqual(compression_errors[0].get("output_path"), "")

    def test_scan_worker_stops_during_incremental_directory_scan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "first.mp4"
            second = Path(tmp) / "second.mp4"
            first.write_bytes(b"1")
            second.write_bytes(b"2")
            worker = ScanWorker([tmp], set())
            completed: list[tuple[list[VideoItem], int]] = []
            worker.finishedScan.connect(lambda items, count: completed.append((items, count)))

            def paths(_root: Path):
                yield first
                worker.cancel()
                yield second

            with patch("video_compress_gui.iter_video_paths", side_effect=paths):
                worker.run()

        self.assertEqual(completed[0][1], 1)
        self.assertEqual([item.path.name for item in completed[0][0]], ["first.mp4"])

    def test_copy_and_saved_diagnostics_include_required_context(self) -> None:
        window = self.make_window()
        self.add_items(window, 1)
        window.active_batch_rows = [0]
        summary = FakeSummary(
            fail_count=1,
            results=[
                FakeResult(
                    state=JobState.FAILED,
                    error="编码失败",
                    diagnostic="ffmpeg exited 1",
                    error_code="encode_failed",
                    output_path=Path("C:/fan-videos/event-0/compressed/clip-0_compressed.mp4"),
                )
            ],
        )
        log_dir = Path(self.settings_dir.name) / "diagnostic-test"

        with patch.dict(os.environ, {"VIDEO_COMPRESSOR_LOG_DIR": str(log_dir)}):
            window.on_finished(summary)
            window.copy_diagnostics()

        payload = json.loads(QApplication.clipboard().text())
        self.assertEqual(payload["app_version"], "1.5.0")
        self.assertTrue(payload["platform"])
        self.assertEqual(payload["profile"], window.profile_id)
        self.assertEqual(payload["output_mode"], window.output_mode_combo.currentData())
        self.assertTrue(payload["tool_status"]["ready"])
        diagnostic = payload["diagnostics"][0]
        self.assertEqual(diagnostic["filename"], "clip-0.mp4")
        self.assertEqual(diagnostic.get("input_path"), str(window.items[0].path))
        self.assertEqual(
            diagnostic.get("output_path"),
            str(Path("C:/fan-videos/event-0/compressed/clip-0_compressed.mp4")),
        )
        self.assertEqual(diagnostic["error_code"], "encode_failed")
        self.assertEqual(diagnostic["summary"], "编码失败")
        saved = log_dir / "gui-diagnostics.jsonl"
        self.assertTrue(saved.exists())
        self.assertEqual(json.loads(saved.read_text(encoding="utf-8").splitlines()[-1])["error_code"], "encode_failed")

    def test_worker_exception_can_be_copied_and_log_directory_opened(self) -> None:
        window = self.make_window()
        log_dir = Path(self.settings_dir.name) / "worker-log-test"
        record = {
            "phase": "compression",
            "filename": "clip.mp4",
            "error_code": "compression_worker_exception",
            "summary": "压缩任务异常: boom",
            "traceback": "trace",
        }

        with patch.dict(os.environ, {"VIDEO_COMPRESSOR_LOG_DIR": str(log_dir)}):
            window.on_worker_error(record)
            self.assertTrue(window.result_bar.copy_btn.isEnabled())
            window.copy_diagnostics()
            with patch("video_compress_gui.QDesktopServices.openUrl") as open_url:
                window.open_log_directory()

        payload = json.loads(QApplication.clipboard().text())
        self.assertEqual(payload["diagnostics"][0]["error_code"], "compression_worker_exception")
        self.assertEqual(payload["diagnostics"][0].get("input_path"), "")
        self.assertEqual(payload["diagnostics"][0].get("output_path"), "")
        open_url.assert_called_once()

    def test_same_named_failures_keep_distinct_input_and_output_paths(self) -> None:
        window = self.make_window()
        summary = FakeSummary(
            fail_count=2,
            results=[
                FakeResult(
                    state=JobState.FAILED,
                    error="编码失败",
                    input_info=SimpleNamespace(path=Path("C:/event-a/clip.mp4")),
                    output_path=Path("C:/event-a/compressed/clip_compressed.mp4"),
                ),
                FakeResult(
                    state=JobState.FAILED,
                    error="编码失败",
                    input_info=SimpleNamespace(path=Path("D:/event-b/clip.mp4")),
                    output_path=Path("D:/event-b/compressed/clip_compressed.mp4"),
                ),
            ],
        )

        window.on_finished(summary)

        self.assertEqual([record["filename"] for record in window._diagnostic_records], ["clip.mp4", "clip.mp4"])
        self.assertEqual(
            [record.get("input_path") for record in window._diagnostic_records],
            [str(Path("C:/event-a/clip.mp4")), str(Path("D:/event-b/clip.mp4"))],
        )
        self.assertEqual(
            [record.get("output_path") for record in window._diagnostic_records],
            [
                str(Path("C:/event-a/compressed/clip_compressed.mp4")),
                str(Path("D:/event-b/compressed/clip_compressed.mp4")),
            ],
        )

    def test_result_directories_only_include_kept_results_with_existing_outputs(self) -> None:
        window = self.make_window()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            success = root / "success" / "clip.mp4"
            skipped = root / "skipped" / "clip.mp4"
            not_smaller = root / "not-smaller" / "clip.mp4"
            failed = root / "failed" / "clip.mp4"
            missing = root / "missing" / "clip.mp4"
            for path in (success, skipped, not_smaller, failed):
                path.parent.mkdir(parents=True)
                path.touch()
            missing.parent.mkdir()

            directories = window.result_directories(
                [
                    FakeResult(success=True, state=JobState.SUCCEEDED, output_path=success),
                    FakeResult(skipped=True, state=JobState.SKIPPED, output_path=skipped),
                    FakeResult(success=True, state=JobState.NOT_SMALLER, output_path=not_smaller),
                    FakeResult(state=JobState.FAILED, output_path=failed),
                    FakeResult(success=True, state=JobState.SUCCEEDED, output_path=missing),
                ]
            )

        self.assertEqual(directories, [success.parent, skipped.parent, not_smaller.parent])

    def test_all_failed_results_do_not_fall_back_to_planned_output_directory(self) -> None:
        window = self.make_window()
        self.add_items(window, 1)
        with tempfile.TemporaryDirectory() as tmp:
            planned = Path(tmp) / "compressed"
            planned.mkdir()
            output = planned / "clip-0_compressed.mp4"
            window.items[0].output_path = output
            window.on_finished(
                FakeSummary(
                    fail_count=1,
                    results=[FakeResult(state=JobState.FAILED, output_path=output, error="失败")],
                )
            )

            with (
                patch("video_compress_gui.QDesktopServices.openUrl") as open_url,
                patch.object(QMessageBox, "information") as information,
            ):
                window.open_result_output_folders()

        open_url.assert_not_called()
        information.assert_called_once()

    def test_drop_panel_accepts_only_existing_local_supported_files_or_directories(self) -> None:
        root = Path(self.settings_dir.name) / "drop-test"
        root.mkdir(exist_ok=True)
        video = root / "clip.mp4"
        video.touch()
        unsupported = root / "notes.txt"
        unsupported.touch()
        missing = root / "missing.mp4"
        urls = [
            QUrl.fromLocalFile(str(video)),
            QUrl.fromLocalFile(str(root)),
            QUrl.fromLocalFile(str(unsupported)),
            QUrl.fromLocalFile(str(missing)),
            QUrl("https://example.com/video.mp4"),
        ]

        class FakeMimeData:
            def hasUrls(self) -> bool:
                return True

            def urls(self) -> list[QUrl]:
                return urls

        class FakeEvent:
            def __init__(self) -> None:
                self.accepted = False
                self.ignored = False

            def mimeData(self) -> FakeMimeData:
                return FakeMimeData()

            def acceptProposedAction(self) -> None:
                self.accepted = True

            def ignore(self) -> None:
                self.ignored = True

        panel = QueueDropPanel()
        dropped: list[list[str]] = []
        panel.filesDropped.connect(dropped.append)
        event = FakeEvent()

        panel.dropEvent(event)

        self.assertTrue(event.accepted)
        self.assertEqual([[Path(path) for path in batch] for batch in dropped], [[video, root]])

        invalid_urls = [QUrl.fromLocalFile(str(unsupported)), QUrl.fromLocalFile(str(missing))]
        urls[:] = invalid_urls
        invalid_event = FakeEvent()
        panel.dragEnterEvent(invalid_event)
        self.assertTrue(invalid_event.ignored)

    def test_worker_reference_is_cleared_only_by_qthread_finished(self) -> None:
        window = self.make_window()
        worker = CompressionWorker([], CompressionOptions())
        window.worker = worker

        with patch.object(QMessageBox, "exec", return_value=QMessageBox.Rejected):
            window.on_finished(FakeSummary())
        self.assertIs(window.worker, worker)

        window.on_worker_thread_finished()
        self.assertIsNone(window.worker)

    def test_qthread_finished_cleanup_refreshes_available_actions(self) -> None:
        window = self.make_window()
        self.add_items(window, 1)
        window.worker = CompressionWorker([], CompressionOptions())
        window.start_btn.setEnabled(False)

        window.on_worker_thread_finished()

        self.assertIsNone(window.worker)
        self.assertTrue(window.start_btn.isEnabled())

    def test_appearance_controls_are_not_in_main_settings_panel(self) -> None:
        window = self.make_window()

        self.assertTrue(window.appearance_btn.isVisibleTo(window))
        self.assertIsNone(window.settings_scroll.findChild(type(window.dim_slider), "MainBackgroundDim"))
        self.assertFalse(window.appearance_dialog.isModal())

    def test_settings_button_check_state_description_and_panel_stay_in_sync(self) -> None:
        window = self.make_window()

        self.assertTrue(window.settings_toggle_btn.isCheckable())
        self.assertEqual(window.settings_toggle_btn.isChecked(), not window.settings_scroll.isHidden())
        self.assertIn("展开", window.settings_toggle_btn.accessibleDescription())

        window.settings_toggle_btn.click()

        self.assertEqual(window.settings_toggle_btn.isChecked(), not window.settings_scroll.isHidden())
        self.assertIn("收起", window.settings_toggle_btn.accessibleDescription())

    def test_sleep_switch_defaults_on_and_persists_on_supported_platforms(self) -> None:
        settings = QSettings("FanTools", "VideoCompressor")
        settings.remove("power/prevent_idle_sleep")
        settings.sync()
        self.addCleanup(settings.remove, "power/prevent_idle_sleep")

        with (
            patch("video_compress_gui.supports_sleep_inhibition", return_value=True),
            patch("video_compress_gui.supports_process_pause", return_value=True),
        ):
            window = self.make_window()
            self.assertFalse(window.sleep_group.isHidden())
            self.assertTrue(window.sleep_switch.isChecked())

            window.sleep_switch.setChecked(False)
            window.save_sleep_setting()
            settings.sync()

            replacement = self.make_window()
            self.assertFalse(replacement.sleep_switch.isChecked())

    def test_power_controls_are_hidden_on_unsupported_platforms(self) -> None:
        with (
            patch("video_compress_gui.supports_sleep_inhibition", return_value=False),
            patch("video_compress_gui.supports_process_pause", return_value=False),
        ):
            window = self.make_window()

        self.assertTrue(window.sleep_group.isHidden())
        self.assertTrue(window.pause_btn.isHidden())

    def test_sleep_switch_is_locked_while_compression_runs(self) -> None:
        window = self.make_window()

        window.set_controls_enabled(False, running=True)

        self.assertFalse(window.sleep_switch.isEnabled())

    def test_pause_button_tracks_encode_pause_and_resume(self) -> None:
        with patch("video_compress_gui.supports_process_pause", return_value=True):
            window = self.make_window()
            self.add_items(window, 1)
            worker = Mock()
            worker.isRunning.return_value = True
            window.worker = worker
            window.current_batch_row = 0
            self.addCleanup(setattr, window, "worker", None)

            window.on_pause_available(True)
            window.pause_btn.click()

            worker.pause.assert_called_once_with()

            window.on_pause_changed(True)

            self.assertEqual(window.pause_btn.text(), "继续")
            self.assertTrue(window.pause_btn.isEnabled())
            self.assertEqual(window.items[0].status, "已暂停")
            self.assertIn("已暂停", window.summary_label.text())

            window.pause_btn.click()

            worker.resume.assert_called_once_with()

    def test_cancel_while_paused_resets_pause_ui(self) -> None:
        window = self.make_window()
        self.add_items(window, 1)
        worker = Mock()
        worker.isRunning.return_value = True
        window.worker = worker
        window.current_batch_row = 0
        self.addCleanup(setattr, window, "worker", None)
        window.on_pause_available(True)
        window.on_pause_changed(True)

        window.cancel_compression()

        self.assertFalse(window.pause_btn.isEnabled())
        self.assertEqual(window.pause_btn.text(), "暂停")
        self.assertEqual(window.pause_btn.accessibleName(), "暂停压缩")
        worker.cancel.assert_called_once_with()

    def test_final_unpaused_cleanup_does_not_overwrite_completed_ui(self) -> None:
        window = self.make_window()
        self.add_items(window, 1)
        worker = Mock()
        worker.isRunning.return_value = True
        window.worker = worker
        window.current_batch_row = 0
        window.items[0].status = "完成"
        window.render_table()
        window.summary_label.setText("压缩完成")
        self.addCleanup(setattr, window, "worker", None)

        window.on_pause_changed(False)

        self.assertEqual(window.items[0].status, "完成")
        self.assertEqual(window.summary_label.text(), "压缩完成")

    def test_start_passes_sleep_switch_value_to_worker_on_supported_platform(self) -> None:
        window = self.make_window()
        self.add_items(window, 1)
        window.sleep_switch.setChecked(True)

        with (
            patch("video_compress_gui.supports_sleep_inhibition", return_value=True),
            patch("video_compress_gui.CompressionWorker") as worker_class,
        ):
            worker = worker_class.return_value
            worker.isRunning.return_value = False
            window.start_compression()

        self.assertTrue(worker_class.call_args.kwargs["prevent_sleep"])

    def test_compression_disables_all_profile_radio_buttons(self) -> None:
        window = self.make_window()

        window.set_controls_enabled(False, running=True)

        self.assertTrue(window.preset_buttons.buttons())
        self.assertTrue(all(not button.isEnabled() for button in window.preset_buttons.buttons()))

    def test_old_core_support_1080p_copy_is_removed(self) -> None:
        window = self.make_window()
        visible_copy = "\n".join(label.text() for label in window.findChildren(type(window.summary_label)))

        self.assertNotIn("core 支持后", visible_copy)

    def test_standard_shortcuts_and_accessible_names_are_present(self) -> None:
        window = self.make_window()
        expected = {
            QKeySequence.StandardKey.Open,
            QKeySequence.StandardKey.Find,
            QKeySequence.StandardKey.SelectAll,
        }
        actual = {shortcut.standard_key for shortcut in window.standard_shortcuts}

        self.assertTrue(expected.issubset(actual))
        self.assertEqual(window.start_btn.accessibleName(), "开始压缩")
        self.assertEqual(window.table.accessibleName(), "视频压缩队列")
        self.assertEqual(
            [shortcut.key() for shortcut in window.remove_shortcuts],
            [QKeySequence(text) for text in remove_shortcut_texts()],
        )
        self.assertEqual(window.remove_shortcut, window.remove_shortcuts[-1])
        self.assertEqual(window.start_shortcut.key(), QKeySequence(start_shortcut_text()))
        self.assertEqual(window.total_progress.accessibleName(), "总压缩进度")

    def test_row_progress_and_result_labels_have_accessible_names(self) -> None:
        window = self.make_window()
        self.add_items(window, 1)

        progress = window.table.cellWidget(0, 4)

        self.assertEqual(progress.accessibleName(), "clip-0.mp4 压缩进度")
        self.assertEqual(window.result_bar.title_label.accessibleName(), "批处理结果")
        self.assertEqual(window.result_bar.detail_label.accessibleName(), "批处理结果详情")

    def test_overwrite_requires_confirmation_before_starting(self) -> None:
        window = self.make_window()
        self.add_items(window, 1)
        window.conflict_combo.setCurrentIndex(window.conflict_combo.findData("overwrite"))

        with (
            patch("video_compress_gui.resolve_ffmpeg_path", return_value="ffmpeg"),
            patch.object(QMessageBox, "question", return_value=QMessageBox.No) as question,
            patch.object(CompressionWorker, "start"),
        ):
            window.start_compression()

        question.assert_called_once()
        self.assertIsNone(window.worker)


if __name__ == "__main__":
    unittest.main()
