# -*- coding: utf-8 -*-
"""PySide6 desktop GUI for 视频压缩工具箱 v1.5."""

from __future__ import annotations

import inspect
import json
import os
import platform
import sys
import traceback
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from compressor_core import (
    APP_NAME,
    APP_VERSION,
    AUDIO_BITRATES,
    DEFAULT_AUDIO_BITRATE,
    DEFAULT_CRF,
    DEFAULT_PRESET,
    PRESETS,
    CancelToken,
    CompressionOptions,
    JobState,
    VideoItem,
    compress_batch,
    format_size,
    is_video_file,
    iter_video_paths,
    parse_crf,
    resolve_ffmpeg_path,
    resolve_ffprobe_path,
    validate_toolchain,
)
from video_compressor.diagnostics import _default_log_dir
from video_compressor.platform_support import (
    remove_shortcut_texts,
    start_shortcut_text,
    supports_process_pause,
    supports_sleep_inhibition,
)
from video_compressor.power import create_sleep_inhibitor

try:
    from PySide6.QtCore import (
        QEasingCurve,
        QPropertyAnimation,
        QRectF,
        QSettings,
        QSize,
        Qt,
        QThread,
        QTimer,
        QUrl,
        Signal,
    )
    from PySide6.QtGui import (
        QAction,
        QColor,
        QDesktopServices,
        QDragEnterEvent,
        QDragLeaveEvent,
        QDragMoveEvent,
        QDropEvent,
        QIcon,
        QImageReader,
        QKeySequence,
        QPainter,
        QPixmap,
        QShortcut,
    )
    from PySide6.QtWidgets import (
        QApplication,
        QAbstractButton,
        QButtonGroup,
        QCheckBox,
        QComboBox,
        QDialog,
        QFileDialog,
        QFrame,
        QGraphicsBlurEffect,
        QGraphicsPixmapItem,
        QGraphicsScene,
        QGridLayout,
        QGroupBox,
        QHBoxLayout,
        QHeaderView,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMenu,
        QMessageBox,
        QProgressBar,
        QPushButton,
        QRadioButton,
        QScrollArea,
        QSizePolicy,
        QSlider,
        QSpinBox,
        QSplitter,
        QStyle,
        QTableWidget,
        QTableWidgetItem,
        QVBoxLayout,
        QWidget,
    )
except ImportError as exc:  # pragma: no cover - local runtime availability
    print("未安装 PySide6，无法启动图形界面。")
    print("请运行: python -m pip install -r requirements.txt")
    print(f"原始错误: {exc}")
    raise SystemExit(1)


@dataclass(frozen=True)
class GuiProfile:
    profile_id: str
    crf: int
    preset: str
    audio_bitrate: str
    max_height: int | None = None


PRESET_OPTIONS = {
    "高清分享": GuiProfile("share", 25, "fast", "128k"),
    "清晰优先": GuiProfile("quality", 23, "medium", "160k"),
    "体积优先": GuiProfile("small", 28, "fast", "96k", 1080),
}

STATUS_COLORS = {
    "完成": QColor("#0f766e"),
    "未变小": QColor("#a15c00"),
    "失败": QColor("#b42318"),
    "分析中": QColor("#2563eb"),
    "压缩中": QColor("#2563eb"),
    "已暂停": QColor("#a15c00"),
    "正在取消": QColor("#a15c00"),
    "已跳过": QColor("#b45309"),
    "已取消": QColor("#b45309"),
    "未执行": QColor("#64748b"),
}


def _exception_diagnostic(phase: str, error_code: str, exc: Exception) -> dict[str, str]:
    return {
        "phase": phase,
        "filename": "",
        "input_path": "",
        "output_path": "",
        "error_code": error_code,
        "summary": f"{type(exc).__name__}: {exc}",
        "traceback": traceback.format_exc(),
    }


def resource_path(relative: str) -> Path:
    """Resolve assets in source checkouts and PyInstaller frozen bundles."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / relative


def _set_drag_state(widget: QWidget, active: bool) -> None:
    widget.setProperty("dragActive", active)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def _local_drop_paths(urls: list[QUrl]) -> list[str]:
    paths: list[str] = []
    for url in urls:
        if not url.isLocalFile():
            continue
        raw_path = url.toLocalFile()
        path = Path(raw_path)
        if path.exists() and (path.is_dir() or is_video_file(path)):
            paths.append(raw_path)
    return paths
    widget.update()


class SwitchButton(QAbstractButton):
    """Compact keyboard-accessible on/off switch."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(44, 24)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)

    def paintEvent(self, _event) -> None:  # type: ignore[no-untyped-def]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        track = QColor("#0f766e" if self.isChecked() else "#94a3b8")
        if not self.isEnabled():
            track.setAlpha(120)
        painter.setPen(Qt.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(QRectF(0, 1, 44, 22), 11, 11)
        knob_x = 23 if self.isChecked() else 3
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(QRectF(knob_x, 3, 18, 18))
        if self.hasFocus():
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QColor("#2563eb"))
            painter.drawRoundedRect(QRectF(0.5, 0.5, 43, 23), 11, 11)


class BackgroundWidget(QWidget):
    """Paints a cached, downsampled local background below the application UI."""

    def __init__(self) -> None:
        super().__init__()
        self.background_path = ""
        self.dim = 62
        self.blur = 8
        self.fit_mode = "cover"
        self._pixmap: QPixmap | None = None
        self._source_key: tuple[str, float] | None = None
        self._cache_key: tuple[Any, ...] | None = None
        self._cache_pixmap: QPixmap | None = None

    def set_background(self, path: str, dim: int, fit_mode: str, blur: int = 8) -> None:
        self.background_path = path
        self.dim = max(0, min(100, int(dim)))
        self.blur = max(0, int(blur))
        self.fit_mode = fit_mode if fit_mode in {"cover", "contain"} else "cover"
        source = Path(path) if path else None
        source_key = None
        if source and source.exists():
            source_key = (str(source.resolve()), source.stat().st_mtime)
        if source_key != self._source_key:
            self._source_key = source_key
            self._pixmap = self._load_downsampled(source) if source_key else None
        self._cache_key = None
        self._cache_pixmap = None
        self.update()

    def _load_downsampled(self, path: Path | None) -> QPixmap | None:
        if not path:
            return None
        reader = QImageReader(str(path))
        size = reader.size()
        max_size = QSize(1920, 1080)
        if size.isValid() and (size.width() > max_size.width() or size.height() > max_size.height()):
            size.scale(max_size, Qt.KeepAspectRatio)
            reader.setScaledSize(size)
        image = reader.read()
        if image.isNull():
            return None
        return QPixmap.fromImage(image)

    def paintEvent(self, event):  # type: ignore[no-untyped-def]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        rect = self.rect()
        if self._pixmap and not self._pixmap.isNull():
            mode = Qt.KeepAspectRatio if self.fit_mode == "contain" else Qt.KeepAspectRatioByExpanding
            key = (self._pixmap.cacheKey(), rect.size(), self.fit_mode, self.blur)
            if key != self._cache_key or not self._cache_pixmap:
                scaled = self._pixmap.scaled(rect.size(), mode, Qt.SmoothTransformation)
                self._cache_pixmap = self.blurred_pixmap(scaled, self.blur) if self.blur else scaled
                self._cache_key = key
            scaled = self._cache_pixmap
            if scaled:
                painter.drawPixmap(
                    (rect.width() - scaled.width()) // 2,
                    (rect.height() - scaled.height()) // 2,
                    scaled,
                )
            painter.fillRect(rect, QColor(15, 23, 42, min(235, self.dim * 2)))
        else:
            painter.fillRect(rect, QColor("#eef3f7"))
            painter.setPen(QColor("#d9e2e8"))
            for x in range(-48, rect.width() + 48, 48):
                painter.drawLine(x, 0, x + rect.height(), rect.height())
            painter.fillRect(rect, QColor(255, 255, 255, 155))
        super().paintEvent(event)

    def blurred_pixmap(self, pixmap: QPixmap, radius: int) -> QPixmap:
        if radius <= 0:
            return pixmap
        scene = QGraphicsScene()
        item = QGraphicsPixmapItem(pixmap)
        effect = QGraphicsBlurEffect()
        effect.setBlurRadius(radius)
        item.setGraphicsEffect(effect)
        scene.addItem(item)
        result = QPixmap(pixmap.size())
        result.fill(Qt.transparent)
        painter = QPainter(result)
        scene.render(painter)
        painter.end()
        return result


class QueueDropPanel(QFrame):
    filesDropped = Signal(list)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("QueuePanel")
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls() and _local_drop_paths(event.mimeData().urls()):
            _set_drag_state(self, True)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if event.mimeData().hasUrls() and _local_drop_paths(event.mimeData().urls()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:
        _set_drag_state(self, False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        _set_drag_state(self, False)
        paths = _local_drop_paths(event.mimeData().urls())
        if paths:
            self.filesDropped.emit(paths)
            event.acceptProposedAction()
        else:
            event.ignore()


class DropTable(QTableWidget):
    filesDropped = Signal(list)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls() and _local_drop_paths(event.mimeData().urls()):
            _set_drag_state(self, True)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if event.mimeData().hasUrls() and _local_drop_paths(event.mimeData().urls()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:
        _set_drag_state(self, False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        _set_drag_state(self, False)
        paths = _local_drop_paths(event.mimeData().urls())
        if paths:
            self.filesDropped.emit(paths)
            event.acceptProposedAction()
        else:
            event.ignore()


class ResultBar(QFrame):
    """Embedded, non-modal batch result and recovery actions."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ResultBar")
        self.result_kind = ""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)
        text_box = QVBoxLayout()
        text_box.setSpacing(2)
        self.title_label = QLabel()
        self.title_label.setObjectName("ResultTitle")
        self.detail_label = QLabel()
        self.detail_label.setObjectName("SmallText")
        self.detail_label.setWordWrap(True)
        text_box.addWidget(self.title_label)
        text_box.addWidget(self.detail_label)
        layout.addLayout(text_box, 1)
        self.open_btn = QPushButton("打开结果")
        self.failures_btn = QPushButton("只看失败")
        self.retry_btn = QPushButton("重试失败")
        self.copy_btn = QPushButton("复制诊断")
        self.logs_btn = QPushButton("日志目录")
        for button in (self.open_btn, self.failures_btn, self.retry_btn, self.copy_btn, self.logs_btn):
            layout.addWidget(button)
        self.hide()

    def show_result(
        self,
        kind: str,
        title: str,
        detail: str,
        has_failures: bool,
        has_diagnostics: bool | None = None,
    ) -> None:
        self.result_kind = kind
        self.setProperty("resultKind", kind)
        self.style().unpolish(self)
        self.style().polish(self)
        self.title_label.setText(title)
        self.detail_label.setText(detail)
        self.failures_btn.setEnabled(has_failures)
        self.retry_btn.setEnabled(has_failures)
        self.copy_btn.setEnabled(has_failures if has_diagnostics is None else has_diagnostics)
        self.show()


class AppearanceDialog(QDialog):
    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window)
        self.window = window
        self.setWindowTitle("外观")
        self.setModal(False)
        self.setMinimumWidth(360)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        buttons = QHBoxLayout()
        window.choose_bg_btn = QPushButton("选择背景图")
        window.reset_bg_btn = QPushButton("恢复默认")
        window.choose_bg_btn.setIcon(window.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon))
        window.reset_bg_btn.setIcon(window.style().standardIcon(QStyle.StandardPixmap.SP_DialogResetButton))
        window.choose_bg_btn.clicked.connect(window.choose_background)
        window.reset_bg_btn.clicked.connect(window.reset_background)
        buttons.addWidget(window.choose_bg_btn)
        buttons.addWidget(window.reset_bg_btn)
        layout.addLayout(buttons)

        window.dim_slider = QSlider(Qt.Horizontal)
        window.dim_slider.setObjectName("AppearanceBackgroundDim")
        window.dim_slider.setRange(25, 90)
        window.dim_slider.setValue(62)
        window.blur_slider = QSlider(Qt.Horizontal)
        window.blur_slider.setObjectName("AppearanceBackgroundBlur")
        window.blur_slider.setRange(0, 24)
        window.blur_slider.setValue(8)
        window.fit_combo = QComboBox()
        window.fit_combo.addItem("铺满窗口", "cover")
        window.fit_combo.addItem("完整显示", "contain")
        layout.addWidget(QLabel("背景暗化"))
        layout.addWidget(window.dim_slider)
        layout.addWidget(QLabel("背景柔化"))
        layout.addWidget(window.blur_slider)
        layout.addWidget(QLabel("显示方式"))
        layout.addWidget(window.fit_combo)
        privacy = QLabel("背景图只保存在本机，不会上传。")
        privacy.setObjectName("SmallText")
        layout.addWidget(privacy)

        window.dim_slider.valueChanged.connect(window.schedule_background_preview)
        window.blur_slider.valueChanged.connect(window.schedule_background_preview)
        window.dim_slider.sliderReleased.connect(window.save_background_settings)
        window.blur_slider.sliderReleased.connect(window.save_background_settings)
        window.fit_combo.currentIndexChanged.connect(window.save_background_settings)


class ToolchainWorker(QThread):
    reportReady = Signal(object)
    workerError = Signal(object)

    def __init__(self, validator: Any) -> None:
        super().__init__()
        self.validator = validator

    def run(self) -> None:
        try:
            report = self.validator(
                ffmpeg=resolve_ffmpeg_path(),
                ffprobe=resolve_ffprobe_path(),
            )
            if not self.isInterruptionRequested():
                self.reportReady.emit(report)
        except Exception as exc:
            if not self.isInterruptionRequested():
                self.workerError.emit(
                    _exception_diagnostic("toolchain", "toolchain_worker_exception", exc)
                )


class CompressionWorker(QThread):
    itemStarted = Signal(int, int)
    itemProgress = Signal(int, int)
    itemStatus = Signal(int, str, str)
    batchProgress = Signal(int, int, int, int, int)
    finishedSummary = Signal(object)
    workerError = Signal(object)
    workComplete = Signal()
    pauseAvailable = Signal(bool)
    pauseChanged = Signal(bool)

    def __init__(
        self,
        items: list[VideoItem],
        options: CompressionOptions,
        prevent_sleep: bool = False,
    ) -> None:
        super().__init__()
        self.items = items
        self.options = options
        self.prevent_sleep = bool(prevent_sleep)
        self.cancel_token = CancelToken()
        self.ffmpeg = resolve_ffmpeg_path()
        self.ffprobe = resolve_ffprobe_path()

    def cancel(self) -> None:
        self.cancel_token.cancel()

    def pause(self) -> None:
        self.cancel_token.pause()

    def resume(self) -> None:
        self.cancel_token.resume()

    def run(self) -> None:
        inhibitor = create_sleep_inhibitor(enabled=self.prevent_sleep)
        try:
            inhibitor.start()
            index_map = {
                str(item.path): int(getattr(item, "gui_row", index))
                for index, item in enumerate(self.items)
            }

            def on_item_start(item, current, total):  # type: ignore[no-untyped-def]
                self.itemStarted.emit(index_map[str(item.path)], total)

            def on_progress(item, percent, elapsed):  # type: ignore[no-untyped-def]
                self.itemProgress.emit(index_map[str(item.path)], int(percent))

            def on_status(item, status):  # type: ignore[no-untyped-def]
                self.itemStatus.emit(index_map[str(item.path)], status, "")

            def on_item_done(item, result, current, total):  # type: ignore[no-untyped-def]
                row = index_map[str(item.path)]
                if result.skipped:
                    self.itemStatus.emit(row, "已跳过", result.error)
                elif result.success and result.state == JobState.NOT_SMALLER:
                    self.itemStatus.emit(row, "未变小", "输出可正常使用，但文件体积没有减小。")
                elif result.success:
                    self.itemStatus.emit(row, "完成", "")
                elif result.cancelled:
                    self.itemStatus.emit(row, "已取消", "")
                else:
                    detail = result.diagnostic or result.error
                    self.itemStatus.emit(row, "失败", detail)

            def on_batch_progress(summary, current, total):  # type: ignore[no-untyped-def]
                self.batchProgress.emit(
                    current,
                    total,
                    summary.success_count,
                    summary.fail_count,
                    summary.skipped_count,
                )

            def on_pause_available(_item, available):  # type: ignore[no-untyped-def]
                if not available:
                    self.cancel_token.resume()
                self.pauseAvailable.emit(bool(available))

            def on_pause_changed(_item, paused):  # type: ignore[no-untyped-def]
                if paused:
                    inhibitor.stop()
                elif not self.cancel_token.is_cancelled():
                    inhibitor.start()
                self.pauseChanged.emit(bool(paused))

            summary = compress_batch(
                self.items,
                self.options,
                callbacks={
                    "item_start": on_item_start,
                    "progress": on_progress,
                    "status": on_status,
                    "item_done": on_item_done,
                    "batch_progress": on_batch_progress,
                    "pause_available": on_pause_available,
                    "pause_changed": on_pause_changed,
                },
                cancel_token=self.cancel_token,
                ffmpeg=self.ffmpeg,
                ffprobe=self.ffprobe,
            )
            self.finishedSummary.emit(summary)
        except Exception as exc:  # keep the GUI recoverable from worker failures
            self.workerError.emit(
                _exception_diagnostic("compression", "compression_worker_exception", exc)
            )
        finally:
            inhibitor.stop()
            self.pauseAvailable.emit(False)
            self.pauseChanged.emit(False)
            self.workComplete.emit()


class ScanWorker(QThread):
    finishedScan = Signal(list, int)
    scanProgress = Signal(int)
    workerError = Signal(object)
    workComplete = Signal()

    def __init__(self, paths: list[str], known: set[str]) -> None:
        super().__init__()
        self.paths = paths
        self.known = known
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        added_items: list[VideoItem] = []
        known = set(self.known)
        try:
            for raw_path in self.paths:
                if self._cancelled:
                    break
                for file_path in iter_video_paths(Path(raw_path).expanduser()):
                    if self._cancelled:
                        break
                    try:
                        resolved = file_path.resolve()
                        size = resolved.stat().st_size
                    except OSError:
                        continue
                    key = str(resolved).lower()
                    if key in known:
                        continue
                    known.add(key)
                    item = VideoItem(resolved, size=size)
                    added_items.append(item)
                    self.scanProgress.emit(len(added_items))
            self.finishedScan.emit(added_items, len(added_items))
        except Exception as exc:
            self.workerError.emit(_exception_diagnostic("scan", "scan_worker_exception", exc))
        finally:
            self.workComplete.emit()


class MainWindow(QMainWindow):
    def __init__(self, toolchain_validator: Any | None = None) -> None:
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.resize(1120, 720)
        self.setMinimumSize(720, 500)
        icon_path = resource_path("assets/app_icon.svg")
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        self.settings = QSettings("FanTools", "VideoCompressor")
        self._toolchain_validator = toolchain_validator or validate_toolchain
        self._toolchain_report: Any = None
        self._toolchain_check_complete = False
        self._diagnostic_records: list[dict[str, Any]] = []
        self.items: list[VideoItem] = []
        self.worker: CompressionWorker | None = None
        self.scan_worker: ScanWorker | None = None
        self.toolchain_worker: ToolchainWorker | None = None
        self.last_output_dir: Path | None = None
        self.last_result_dirs: list[Path] = []
        self.active_batch_rows: list[int] = []
        self.completed_in_batch = 0
        self.current_batch_row: int | None = None
        self.profile_id = "share"
        self.failed_only = False
        self._applying_preset = False
        self._last_summary: Any = None
        self._compression_summary_received = False
        self.cancel_requested = False
        self._compression_paused = False
        self._pause_available = False
        self.scan_cancel_requested = False
        self._scan_cursor_active = False
        self._close_when_idle = False
        self._settings_user_visible = True
        self._background_path = ""
        self._custom_output_dir = self.settings.value("output/directory", "", str)

        self.background_preview_timer = QTimer(self)
        self.background_preview_timer.setSingleShot(True)
        self.background_preview_timer.setInterval(90)
        self.background_preview_timer.timeout.connect(self.preview_background)

        self.background = BackgroundWidget()
        self.setCentralWidget(self.background)
        self.build_ui()
        self.load_settings()
        self.refresh_action_states()
        self.start_toolchain_check()

    def build_ui(self) -> None:
        root = QVBoxLayout(self.background)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(8)

        title_surface = QFrame()
        title_surface.setObjectName("TitleSurface")
        top = QHBoxLayout(title_surface)
        top.setContentsMargins(16, 10, 12, 10)
        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        self.title = QLabel(APP_NAME)
        self.title.setObjectName("AppTitle")
        self.subtitle = QLabel("拖入 → 选档位 → 开始 → 查看结果")
        self.subtitle.setObjectName("SubTitle")
        title_box.addWidget(self.title)
        title_box.addWidget(self.subtitle)
        top.addLayout(title_box, 1)

        self.ffmpeg_label = QLabel("工具链检测中")
        self.ffmpeg_label.setObjectName("Pill")
        self.settings_toggle_btn = QPushButton("设置")
        self.settings_toggle_btn.setCheckable(True)
        self.settings_toggle_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView))
        self.settings_toggle_btn.clicked.connect(self.toggle_settings_panel)
        self.appearance_btn = QPushButton("外观")
        self.appearance_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DesktopIcon))
        self.appearance_btn.clicked.connect(self.show_appearance_dialog)
        top.addWidget(self.ffmpeg_label)
        top.addWidget(self.settings_toggle_btn)
        top.addWidget(self.appearance_btn)
        root.addWidget(title_surface)

        self.content_splitter = QSplitter(Qt.Horizontal)
        self.content_splitter.setChildrenCollapsible(False)
        root.addWidget(self.content_splitter, 1)

        self.queue_panel = QueueDropPanel()
        self.queue_panel.filesDropped.connect(self.add_paths)
        queue_layout = QVBoxLayout(self.queue_panel)
        queue_layout.setContentsMargins(12, 12, 12, 12)
        queue_layout.setSpacing(8)

        self.drop_hint = QLabel("拖入视频或文件夹")
        self.drop_hint.setObjectName("DropHint")
        self.drop_hint.setAlignment(Qt.AlignCenter)
        self.drop_hint.setMinimumHeight(48)
        queue_layout.addWidget(self.drop_hint)

        self.scan_bar = QFrame()
        self.scan_bar.setObjectName("ScanBar")
        scan_layout = QHBoxLayout(self.scan_bar)
        scan_layout.setContentsMargins(8, 4, 8, 4)
        self.scan_label = QLabel("正在扫描，已发现 0 个")
        self.cancel_scan_btn = QPushButton("取消扫描")
        self.cancel_scan_btn.clicked.connect(self.cancel_scan)
        scan_layout.addWidget(self.scan_label, 1)
        scan_layout.addWidget(self.cancel_scan_btn)
        self.scan_bar.hide()
        queue_layout.addWidget(self.scan_bar)

        queue_top = QHBoxLayout()
        queue_top.setSpacing(8)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索文件名")
        self.search_input.setAccessibleName("搜索队列")
        self.search_input.textChanged.connect(self.apply_search)
        self.add_files_btn = QPushButton("添加视频")
        self.add_folder_btn = QPushButton("添加文件夹")
        self.remove_btn = QPushButton("移除高亮")
        self.add_files_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon))
        self.add_folder_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon))
        self.remove_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon))
        self.add_files_btn.clicked.connect(self.add_files)
        self.add_folder_btn.clicked.connect(self.add_folder)
        self.remove_btn.clicked.connect(self.remove_selected_rows)
        queue_top.addWidget(self.search_input, 1)
        queue_top.addWidget(self.add_files_btn)
        queue_top.addWidget(self.add_folder_btn)
        queue_top.addWidget(self.remove_btn)
        queue_layout.addLayout(queue_top)

        self.table = DropTable()
        self.table.setAccessibleName("视频压缩队列")
        self.table.filesDropped.connect(self.add_paths)
        self.table.itemChanged.connect(lambda _item: self.update_summary())
        self.table.itemSelectionChanged.connect(self.refresh_action_states)
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(["选择", "文件", "大小", "状态", "进度", "输出"])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(44)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.Stretch)
        queue_layout.addWidget(self.table, 1)

        queue_actions = QHBoxLayout()
        self.select_all_btn = QPushButton("全选搜索结果")
        self.select_none_btn = QPushButton("取消选择搜索结果")
        self.open_output_btn = QPushButton("打开结果")
        self.open_all_outputs_btn = QPushButton("结果目录")
        self.open_output_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon))
        self.open_all_outputs_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon))
        self.select_all_btn.clicked.connect(lambda: self.set_all_checked(True))
        self.select_none_btn.clicked.connect(lambda: self.set_all_checked(False))
        self.open_output_btn.clicked.connect(self.open_output_folder)
        self.open_all_outputs_btn.clicked.connect(self.open_all_output_folders)
        queue_actions.addWidget(self.select_all_btn)
        queue_actions.addWidget(self.select_none_btn)
        queue_actions.addStretch(1)
        queue_actions.addWidget(self.open_all_outputs_btn)
        queue_actions.addWidget(self.open_output_btn)
        queue_layout.addLayout(queue_actions)
        self.content_splitter.addWidget(self.queue_panel)

        self.settings_scroll = QScrollArea()
        self.settings_scroll.setObjectName("SettingsScroll")
        self.settings_scroll.setWidgetResizable(True)
        self.settings_scroll.setFrameShape(QFrame.NoFrame)
        settings_body = QWidget()
        settings_layout = QVBoxLayout(settings_body)
        settings_layout.setContentsMargins(10, 4, 6, 4)
        settings_layout.setSpacing(8)

        preset_group = self.group("压缩档位")
        preset_layout = QVBoxLayout(preset_group)
        self.profile_label = QLabel("高清分享")
        self.profile_label.setObjectName("ProfileLabel")
        preset_layout.addWidget(self.profile_label)
        self.preset_buttons = QButtonGroup(self)
        for index, (label, profile) in enumerate(PRESET_OPTIONS.items()):
            button = QRadioButton(label)
            button.setProperty("profileId", profile.profile_id)
            button.setChecked(index == 0)
            self.preset_buttons.addButton(button, index)
            preset_layout.addWidget(button)
        self.preset_buttons.buttonClicked.connect(self.apply_preset)
        settings_layout.addWidget(preset_group)

        self.output_group = self.group("输出位置")
        output_layout = QVBoxLayout(self.output_group)
        output_layout.setSpacing(8)
        self.output_mode_combo = QComboBox()
        self.output_mode_combo.addItem("源文件旁", "source_subfolder")
        self.output_mode_combo.addItem("指定文件夹", "custom")
        self.output_mode_combo.setAccessibleName("输出位置模式")
        output_layout.addWidget(self.output_mode_combo)

        output_path_row = QHBoxLayout()
        output_path_row.setSpacing(6)
        self.output_dir_edit = QLineEdit()
        self.output_dir_edit.setPlaceholderText("每个源文件旁的 compressed 文件夹（可直接输入修改）")
        self.output_dir_edit.setAccessibleName("输出文件夹")
        self.output_dir_btn = QPushButton()
        self.output_dir_btn.setToolTip("选择输出文件夹")
        self.output_dir_btn.setAccessibleName("选择输出文件夹")
        self.output_dir_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon))
        self.output_dir_btn.setFixedWidth(36)
        output_path_row.addWidget(self.output_dir_edit, 1)
        output_path_row.addWidget(self.output_dir_btn)
        output_layout.addLayout(output_path_row)
        output_help = QLabel("默认在每个视频旁创建 compressed；输入路径即可集中保存。")
        output_help.setObjectName("SmallText")
        output_help.setWordWrap(True)
        output_layout.addWidget(output_help)
        settings_layout.addWidget(self.output_group)

        self.sleep_group = self.group("电源")
        sleep_layout = QVBoxLayout(self.sleep_group)
        sleep_layout.setSpacing(6)
        sleep_row = QHBoxLayout()
        sleep_row.addWidget(QLabel("压缩时防止自动睡眠"), 1)
        self.sleep_switch = SwitchButton()
        self.sleep_switch.setAccessibleName("压缩时防止自动睡眠")
        self.sleep_switch.setToolTip("防止因长时间无操作而自动睡眠")
        sleep_row.addWidget(self.sleep_switch)
        sleep_layout.addLayout(sleep_row)
        sleep_help = QLabel("防止因长时间无操作而自动睡眠；屏幕仍可关闭，主动睡眠不受影响。")
        sleep_help.setObjectName("SmallText")
        sleep_help.setWordWrap(True)
        sleep_layout.addWidget(sleep_help)
        self.sleep_group.setVisible(supports_sleep_inhibition())
        settings_layout.addWidget(self.sleep_group)

        self.advanced_toggle = QCheckBox("高级设置")
        self.advanced_toggle.setAccessibleName("展开高级设置")
        settings_layout.addWidget(self.advanced_toggle)
        self.advanced = self.group("编码设置")
        advanced_layout = QGridLayout(self.advanced)
        advanced_layout.setHorizontalSpacing(8)
        advanced_layout.setVerticalSpacing(8)
        self.crf_spin = QSpinBox()
        self.crf_spin.setRange(0, 51)
        self.crf_spin.setValue(DEFAULT_CRF)
        self.preset_combo = QComboBox()
        self.preset_combo.addItems(PRESETS)
        self.preset_combo.setCurrentText(DEFAULT_PRESET)
        self.audio_combo = QComboBox()
        self.audio_combo.addItems(AUDIO_BITRATES)
        self.audio_combo.setCurrentText(DEFAULT_AUDIO_BITRATE)
        advanced_layout.addWidget(QLabel("画质"), 0, 0)
        advanced_layout.addWidget(self.crf_spin, 0, 1, 1, 2)
        advanced_layout.addWidget(QLabel("速度"), 1, 0)
        advanced_layout.addWidget(self.preset_combo, 1, 1, 1, 2)
        advanced_layout.addWidget(QLabel("声音"), 2, 0)
        advanced_layout.addWidget(self.audio_combo, 2, 1, 1, 2)

        self.conflict_combo = QComboBox()
        self.conflict_combo.addItem("已存在时跳过", "skip")
        self.conflict_combo.addItem("覆盖旧文件", "overwrite")
        self.conflict_combo.addItem("自动重命名", "rename")
        advanced_layout.addWidget(QLabel("冲突"), 3, 0)
        advanced_layout.addWidget(self.conflict_combo, 3, 1, 1, 2)
        help_label = QLabel("默认保留原分辨率；体积优先最高限制到 1080p。")
        help_label.setObjectName("SmallText")
        help_label.setWordWrap(True)
        advanced_layout.addWidget(help_label, 4, 0, 1, 3)
        self.advanced.hide()
        self.advanced_toggle.toggled.connect(self.advanced.setVisible)
        settings_layout.addWidget(self.advanced)
        settings_layout.addStretch(1)
        self.settings_scroll.setWidget(settings_body)
        self.settings_scroll.setMinimumWidth(272)
        self.content_splitter.addWidget(self.settings_scroll)
        self.content_splitter.setStretchFactor(0, 4)
        self.content_splitter.setStretchFactor(1, 1)
        self.content_splitter.setSizes([780, 300])

        for control in (
            self.crf_spin,
            self.preset_combo,
            self.audio_combo,
        ):
            if isinstance(control, QSpinBox):
                control.valueChanged.connect(self.mark_custom_profile)
            else:
                control.currentIndexChanged.connect(self.mark_custom_profile)
        self.output_mode_combo.currentIndexChanged.connect(self.update_output_controls)
        self.output_dir_edit.textEdited.connect(self.activate_custom_output)
        self.output_dir_edit.textChanged.connect(self.on_output_directory_changed)
        self.conflict_combo.currentIndexChanged.connect(self.refresh_output_preview)
        self.output_dir_btn.clicked.connect(self.choose_output_directory)
        self.sleep_switch.toggled.connect(self.save_sleep_setting)

        self.result_bar = ResultBar(self.background)
        self.result_bar.title_label.setAccessibleName("批处理结果")
        self.result_bar.detail_label.setAccessibleName("批处理结果详情")
        self.result_bar.open_btn.clicked.connect(self.open_result_output_folders)
        self.result_bar.failures_btn.clicked.connect(self.show_failed_items)
        self.result_bar.retry_btn.clicked.connect(self.retry_failed_items)
        self.result_bar.copy_btn.clicked.connect(self.copy_diagnostics)
        self.result_bar.logs_btn.clicked.connect(self.open_log_directory)
        root.addWidget(self.result_bar)

        self.bottom_bar = QFrame()
        self.bottom_bar.setObjectName("BottomBar")
        bottom_layout = QHBoxLayout(self.bottom_bar)
        bottom_layout.setContentsMargins(12, 8, 12, 8)
        bottom_layout.setSpacing(8)
        bottom_layout.setAlignment(Qt.AlignVCenter)
        self.summary_label = QLabel("拖入视频或文件夹开始")
        self.summary_label.setMinimumWidth(180)
        self.summary_label.setFixedHeight(34)
        self.summary_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.total_progress = QProgressBar()
        self.total_progress.setObjectName("TotalProgress")
        self.total_progress.setRange(0, 100)
        self.total_progress.setValue(0)
        self.total_progress.setFixedHeight(18)
        self.total_progress.setAccessibleName("总压缩进度")
        self.pause_btn = QPushButton("暂停")
        self.pause_btn.setFixedHeight(34)
        self.pause_btn.setEnabled(False)
        self.pause_btn.setAccessibleName("暂停压缩")
        self.pause_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause))
        self.pause_btn.setVisible(supports_process_pause())
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setFixedHeight(34)
        self.cancel_btn.setEnabled(False)
        self.start_btn = QPushButton("开始压缩")
        self.start_btn.setFixedHeight(34)
        self.start_btn.setObjectName("PrimaryButton")
        self.start_btn.setAccessibleName("开始压缩")
        self.start_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.cancel_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserStop))
        self.start_btn.clicked.connect(self.start_compression)
        self.pause_btn.clicked.connect(self.toggle_pause)
        self.cancel_btn.clicked.connect(self.cancel_compression)
        bottom_layout.addWidget(self.summary_label, 2, Qt.AlignVCenter)
        bottom_layout.addWidget(self.total_progress, 2, Qt.AlignVCenter)
        bottom_layout.addWidget(self.pause_btn, 0, Qt.AlignVCenter)
        bottom_layout.addWidget(self.cancel_btn, 0, Qt.AlignVCenter)
        bottom_layout.addWidget(self.start_btn, 0, Qt.AlignVCenter)
        root.addWidget(self.bottom_bar)
        self.update_output_controls()

        self.appearance_dialog = AppearanceDialog(self)
        self.create_shortcuts()
        self.apply_styles()
        self.fade_in()
        self.update_settings_visibility()

    def panel(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("Panel")
        frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        return frame

    def group(self, title: str) -> QGroupBox:
        box = QGroupBox(title)
        box.setObjectName("CleanGroup")
        return box

    def create_shortcuts(self) -> None:
        self.standard_shortcuts: list[QShortcut] = []
        bindings = (
            (QKeySequence.StandardKey.Open, self.add_files),
            (QKeySequence.StandardKey.Find, self.focus_search),
            (QKeySequence.StandardKey.SelectAll, lambda: self.set_all_checked(True)),
        )
        for standard_key, callback in bindings:
            shortcut = QShortcut(QKeySequence(standard_key), self)
            shortcut.standard_key = standard_key  # type: ignore[attr-defined]
            shortcut.activated.connect(callback)
            self.standard_shortcuts.append(shortcut)
        self.remove_shortcuts: list[QShortcut] = []
        for shortcut_text in remove_shortcut_texts():
            shortcut = QShortcut(QKeySequence(shortcut_text), self)
            shortcut.activated.connect(self.remove_selected_rows)
            self.remove_shortcuts.append(shortcut)
        self.remove_shortcut = self.remove_shortcuts[-1]
        self.start_shortcut = QShortcut(QKeySequence(start_shortcut_text()), self)
        self.start_shortcut.activated.connect(self.start_compression)

    def apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QWidget {
                color: #17212b;
                font-family: "Microsoft YaHei UI", "PingFang SC", "Segoe UI";
                font-size: 13px;
                letter-spacing: 0;
            }
            #TitleSurface, #QueuePanel, #BottomBar, #ResultBar {
                background: rgba(248, 250, 252, 0.80);
                border: 1px solid rgba(148, 163, 184, 0.55);
                border-radius: 8px;
            }
            #QueuePanel[dragActive="true"], QTableWidget[dragActive="true"] {
                border: 2px solid #0f766e;
                background: rgba(236, 253, 245, 0.90);
            }
            #AppTitle { font-size: 22px; font-weight: 700; }
            #SubTitle, #SmallText { color: #5b6875; }
            #Pill, #ProfileLabel {
                padding: 5px 8px;
                border-radius: 8px;
                background: #e8eef3;
                color: #334155;
                font-weight: 600;
            }
            #DropHint {
                border: 1px dashed #7b8b99;
                border-radius: 8px;
                background: rgba(255, 255, 255, 0.7);
                color: #415160;
                font-size: 15px;
                font-weight: 600;
            }
            #ScanBar { background: #eaf4ff; border-radius: 6px; }
            QGroupBox#CleanGroup {
                border: 0;
                border-top: 1px solid #d8e0e7;
                margin-top: 12px;
                padding-top: 12px;
                font-weight: 600;
            }
            QGroupBox#CleanGroup::title { subcontrol-origin: margin; padding-right: 6px; }
            QPushButton {
                border: 1px solid #b7c2cc;
                border-radius: 6px;
                padding: 7px 10px;
                background: #ffffff;
            }
            QPushButton:hover { background: #f1f5f9; border-color: #64748b; }
            QPushButton:focus, QLineEdit:focus, QComboBox:focus, QSpinBox:focus,
            QTableWidget:focus, QSlider:focus {
                border: 2px solid #2563eb;
            }
            QPushButton:disabled { color: #94a3b8; background: #edf1f4; }
            QPushButton#PrimaryButton {
                background: #0f766e;
                color: white;
                border: 1px solid #0f766e;
                font-weight: 700;
                padding: 8px 14px;
            }
            QPushButton#PrimaryButton:hover { background: #0b625c; }
            QLineEdit, QComboBox, QSpinBox {
                border: 1px solid #b7c2cc;
                border-radius: 6px;
                padding: 6px 8px;
                background: #ffffff;
            }
            QScrollArea#SettingsScroll { background: rgba(248, 250, 252, 0.80); border-radius: 8px; }
            QScrollArea#SettingsScroll > QWidget > QWidget { background: transparent; }
            QTableWidget {
                border: 1px solid #c4ced7;
                border-radius: 6px;
                background: rgba(255, 255, 255, 0.84);
                gridline-color: #e2e8f0;
                alternate-background-color: rgba(247, 249, 251, 0.82);
                selection-background-color: #dbeafe;
                selection-color: #17212b;
            }
            QHeaderView::section {
                background: rgba(238, 242, 246, 0.90);
                border: 0;
                border-bottom: 1px solid #cbd5e1;
                padding: 7px;
                font-weight: 600;
            }
            QProgressBar {
                border: 0;
                border-radius: 5px;
                background: #dbe3ea;
                min-height: 14px;
                max-height: 14px;
                text-align: center;
            }
            QProgressBar::chunk { border-radius: 5px; background: #0f766e; }
            QProgressBar#TotalProgress { min-height: 18px; max-height: 18px; }
            QSlider::groove:horizontal { height: 6px; border-radius: 3px; background: #d5dee6; }
            QSlider::handle:horizontal {
                width: 16px; margin: -5px 0; border-radius: 8px; background: #2563eb;
            }
            #ResultBar[resultKind="success"] { border-left: 5px solid #0f766e; }
            #ResultBar[resultKind="partial"], #ResultBar[resultKind="cancelled"],
            #ResultBar[resultKind="all_skipped"], #ResultBar[resultKind="not_smaller"] {
                border-left: 5px solid #d97706;
            }
            #ResultBar[resultKind="failure"] { border-left: 5px solid #b42318; }
            #ResultTitle { font-weight: 700; }
            """
        )

    def fade_in(self) -> None:
        self.setWindowOpacity(0.0)
        self.animation = QPropertyAnimation(self, b"windowOpacity")
        self.animation.setDuration(180)
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(1.0)
        self.animation.setEasingCurve(QEasingCurve.OutCubic)
        self.animation.start()

    def load_settings(self) -> None:
        self._background_path = self.settings.value("background/path", "", str)
        dim = int(self.settings.value("background/dim", 62))
        blur = int(self.settings.value("background/blur", 8))
        fit = self.settings.value("background/fit", "cover", str)
        self.dim_slider.setValue(dim)
        self.blur_slider.setValue(blur)
        fit_index = self.fit_combo.findData(fit)
        if fit_index >= 0:
            self.fit_combo.setCurrentIndex(fit_index)
        self.background.set_background(self._background_path, dim, fit, blur)

        saved_output_mode = self.settings.value("output/mode", "source_subfolder", str)
        output_index = self.output_mode_combo.findData(saved_output_mode)
        if output_index < 0:
            output_index = self.output_mode_combo.findData("source_subfolder")
        self.output_mode_combo.blockSignals(True)
        self.output_mode_combo.setCurrentIndex(max(0, output_index))
        self.output_mode_combo.blockSignals(False)
        self.update_output_controls()

        saved_sleep_setting = self.settings.value("power/prevent_idle_sleep", True)
        prevent_idle_sleep = saved_sleep_setting in (True, 1, "1", "true", "True", "yes")
        self.sleep_switch.blockSignals(True)
        self.sleep_switch.setChecked(prevent_idle_sleep)
        self.sleep_switch.blockSignals(False)

        saved_profile = self.settings.value("compression/profile_id", "share", str)
        for button in self.preset_buttons.buttons():
            if button.property("profileId") == saved_profile:
                button.setChecked(True)
                self.apply_preset()
                break
        else:
            if saved_profile == "custom":
                self.profile_id = "custom"
                self.profile_label.setText("自定义")

    def save_sleep_setting(self, *_args: Any) -> None:
        self.settings.setValue("power/prevent_idle_sleep", self.sleep_switch.isChecked())

    def schedule_background_preview(self) -> None:
        self.background_preview_timer.start()

    def preview_background(self) -> None:
        self.background.set_background(
            self._background_path,
            self.dim_slider.value(),
            self.fit_combo.currentData() or "cover",
            self.blur_slider.value(),
        )

    def save_background_settings(self) -> None:
        fit = self.fit_combo.currentData() or "cover"
        self.settings.setValue("background/path", self._background_path)
        self.settings.setValue("background/dim", self.dim_slider.value())
        self.settings.setValue("background/blur", self.blur_slider.value())
        self.settings.setValue("background/fit", fit)
        self.preview_background()

    def choose_background(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择背景图",
            "",
            "图片文件 (*.png *.jpg *.jpeg *.webp *.bmp)",
        )
        if path:
            self._background_path = path
            self.save_background_settings()

    def reset_background(self) -> None:
        self._background_path = ""
        self.dim_slider.setValue(62)
        self.blur_slider.setValue(8)
        self.fit_combo.setCurrentIndex(max(0, self.fit_combo.findData("cover")))
        self.save_background_settings()

    def show_appearance_dialog(self) -> None:
        self.appearance_dialog.show()
        self.appearance_dialog.raise_()
        self.appearance_dialog.activateWindow()

    def start_toolchain_check(self, force: bool = False) -> None:
        if self.toolchain_worker and self.toolchain_worker.isRunning():
            return
        if self._toolchain_report is not None and not force:
            self._show_toolchain_report(self._toolchain_report)
            return
        self._toolchain_report = None
        self._toolchain_check_complete = False
        self.ffmpeg_label.setText("工具链检测中")
        self.ffmpeg_label.setStyleSheet("")
        self.ffmpeg_label.setToolTip("正在检查 FFmpeg、FFprobe 和编码能力，请稍候。")
        self.ffmpeg_label.setAccessibleName("工具状态: 检测中")
        self.refresh_action_states()

        worker = ToolchainWorker(self._toolchain_validator)
        self.toolchain_worker = worker
        worker.reportReady.connect(self.on_toolchain_ready)
        worker.workerError.connect(self.on_toolchain_error)
        worker.finished.connect(self.on_toolchain_thread_finished)
        worker.start()

    def refresh_ffmpeg_state(self, force: bool = False) -> Any:
        if force or self._toolchain_report is None:
            self.start_toolchain_check(force=force)
        elif self._toolchain_report is not None:
            self._show_toolchain_report(self._toolchain_report)
        return self._toolchain_report

    def on_toolchain_ready(self, report: object) -> None:
        self._toolchain_report = report
        self._toolchain_check_complete = True
        self._show_toolchain_report(report)
        self.refresh_action_states()

    def _show_toolchain_report(self, report: Any) -> None:
        checks = (
            ("FFmpeg", report.ffmpeg_available),
            ("FFprobe", report.ffprobe_available),
            ("libx264", report.libx264),
            ("AAC", report.aac),
            ("zscale", report.zscale),
            ("tonemap", report.tonemap),
        )
        missing = [name for name, available in checks if not available]
        if report.ok:
            self.ffmpeg_label.setText("工具链已就绪")
            self.ffmpeg_label.setStyleSheet("")
        else:
            self.ffmpeg_label.setText("工具链不完整")
            self.ffmpeg_label.setStyleSheet("background: #fff1f0; color: #9f1d1d;")
        detail = "全部检查通过" if not missing else f"缺少: {', '.join(missing)}"
        if report.diagnostic:
            detail = f"{detail}\n{report.diagnostic}"
        self.ffmpeg_label.setToolTip(detail)
        self.ffmpeg_label.setAccessibleName(f"工具状态: {detail}")

    def on_toolchain_error(self, diagnostic: object) -> None:
        self._toolchain_check_complete = True
        record = self._store_diagnostic(diagnostic, phase="toolchain")
        detail = str(record.get("summary") or "工具链检测失败")
        self.ffmpeg_label.setText("工具链检测失败")
        self.ffmpeg_label.setStyleSheet("background: #fff1f0; color: #9f1d1d;")
        self.ffmpeg_label.setToolTip(detail)
        self.ffmpeg_label.setAccessibleName(f"工具状态: {detail}")
        self.refresh_action_states()

    def on_toolchain_thread_finished(self) -> None:
        sender = self.sender()
        if sender is None or sender is self.toolchain_worker:
            self.toolchain_worker = None
        self.refresh_action_states()
        self._finish_deferred_close()

    def apply_preset(self) -> None:
        button = self.preset_buttons.checkedButton()
        if not button:
            return
        profile = PRESET_OPTIONS.get(button.text())
        if not profile:
            return
        self._applying_preset = True
        self.crf_spin.setValue(profile.crf)
        self.preset_combo.setCurrentText(profile.preset)
        self.audio_combo.setCurrentText(profile.audio_bitrate)
        self._applying_preset = False
        self.profile_id = profile.profile_id
        self.profile_label.setText(button.text())
        self.settings.setValue("compression/profile_id", self.profile_id)
        self.refresh_output_preview()

    def mark_custom_profile(self, *_args: Any) -> None:
        if self._applying_preset:
            return
        self.profile_id = "custom"
        self.profile_label.setText("自定义")
        self.settings.setValue("compression/profile_id", "custom")

    def current_options(self) -> CompressionOptions:
        values: dict[str, Any] = {
            "crf": parse_crf(self.crf_spin.value()),
            "preset": self.preset_combo.currentText(),
            "audio_bitrate": self.audio_combo.currentText(),
            "conflict_policy": self.conflict_combo.currentData() or "skip",
            "output_mode": self.output_mode_combo.currentData() or "source_subfolder",
            "output_dir": Path(self.output_dir_edit.text().strip()) if self.output_dir_edit.text().strip() else None,
        }
        parameters = inspect.signature(CompressionOptions).parameters
        profile = next(
            (profile for profile in PRESET_OPTIONS.values() if profile.profile_id == self.profile_id),
            None,
        )
        if "profile_id" in parameters:
            values["profile_id"] = self.profile_id
        if "max_height" in parameters:
            values["max_height"] = profile.max_height if profile else None
        supported = {name: value for name, value in values.items() if name in parameters}
        return CompressionOptions(**supported)

    def add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "添加视频",
            "",
            "视频文件 (*.mp4 *.avi *.mkv *.mov *.wmv *.flv *.webm)",
        )
        if paths:
            self.add_paths(paths)

    def add_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "添加文件夹")
        if path:
            self.add_paths([path])

    def add_paths(self, paths: list[str]) -> None:
        if self.is_busy() or not paths:
            return
        self.result_bar.hide()
        self.scan_cancel_requested = False
        self.scan_label.setText("正在扫描，已发现 0 个")
        self.scan_bar.show()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        self._scan_cursor_active = True
        known = {str(item.path).lower() for item in self.items}
        self.set_controls_enabled(False, scanning=True)
        worker = ScanWorker(paths, known)
        self.scan_worker = worker
        worker.scanProgress.connect(self.on_scan_progress)
        worker.finishedScan.connect(self.on_scan_finished)
        worker.workerError.connect(self.on_scan_error)
        worker.workComplete.connect(self.on_scan_work_complete)
        worker.finished.connect(self.on_scan_thread_finished)
        worker.start()

    def on_scan_progress(self, found: int) -> None:
        if self.scan_cancel_requested:
            return
        self.scan_label.setText(f"正在扫描，已发现 {found} 个")

    def cancel_scan(self) -> None:
        if self.scan_worker and self.scan_worker.isRunning():
            self.scan_cancel_requested = True
            self.scan_worker.cancel()
            self.cancel_scan_btn.setEnabled(False)
            self.scan_label.setText("正在取消扫描...")

    def on_scan_finished(self, added_items: list[VideoItem], added: int) -> None:
        for item in added_items:
            item.output_path = self.preview_output_path(item)
            self.items.append(item)
        self.render_table()
        if self.scan_cancel_requested:
            self.summary_label.setText("扫描已取消")
        else:
            self.summary_label.setText(f"本次发现 {added} 个，共 {len(self.items)} 个视频")

    def on_scan_error(self, diagnostic: object) -> None:
        record = self._store_diagnostic(diagnostic, phase="scan")
        message = str(record.get("summary") or "扫描异常")
        self.summary_label.setText("扫描失败")
        self.result_bar.show_result("failure", "扫描失败", message, False, True)

    def on_scan_work_complete(self) -> None:
        if self._scan_cursor_active:
            QApplication.restoreOverrideCursor()
            self._scan_cursor_active = False
        self.scan_bar.hide()
        self.cancel_scan_btn.setEnabled(True)
        if not self._close_when_idle:
            self.set_controls_enabled(True)

    def on_scan_thread_finished(self) -> None:
        sender = self.sender()
        if sender is None or sender is self.scan_worker:
            self.scan_worker = None
        self.refresh_action_states()
        self._finish_deferred_close()

    def preview_output_path(self, item: VideoItem) -> Path:
        from compressor_core import build_output_path

        return build_output_path(item, self.current_options())

    def render_table(self) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.items))
        self.drop_hint.setVisible(not self.items)
        for row, item in enumerate(self.items):
            output_path = item.output_path or self.preview_output_path(item)
            check = QTableWidgetItem("")
            check.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            check.setCheckState(Qt.Checked if item.selected else Qt.Unchecked)
            self.table.setItem(row, 0, check)

            file_cell = QTableWidgetItem(f"{item.path.name}\n{item.path.parent}")
            file_cell.setToolTip(
                f"父目录: {item.path.parent}\n输入: {item.path}\n输出: {output_path}"
            )
            file_cell.setData(Qt.AccessibleTextRole, item.path.name)
            file_cell.setData(Qt.AccessibleDescriptionRole, f"父目录 {item.path.parent}")
            self.table.setItem(row, 1, file_cell)
            self.table.setItem(row, 2, QTableWidgetItem(format_size(item.size)))
            status_cell = QTableWidgetItem(item.status or "待压缩")
            status_cell.setForeground(STATUS_COLORS.get(item.status, QColor("#334155")))
            self.table.setItem(row, 3, status_cell)
            progress = QProgressBar()
            progress.setRange(0, 100)
            progress.setValue(item.progress)
            progress.setFormat("%p%")
            progress.setAccessibleName(f"{item.path.name} 压缩进度")
            self.table.setCellWidget(row, 4, progress)
            output_cell = QTableWidgetItem(str(output_path))
            output_cell.setToolTip(f"输入: {item.path}\n输出: {output_path}")
            self.table.setItem(row, 5, output_cell)
        self.table.blockSignals(False)
        self.apply_search(self.search_input.text())
        self.update_summary()
        self.refresh_action_states()

    def refresh_output_preview(self, *_args: Any) -> None:
        for row, item in enumerate(self.items):
            item.output_path = self.preview_output_path(item)
            output_cell = self.table.item(row, 5)
            file_cell = self.table.item(row, 1)
            if output_cell:
                output_cell.setText(str(item.output_path))
                output_cell.setToolTip(f"输入: {item.path}\n输出: {item.output_path}")
            if file_cell:
                file_cell.setToolTip(
                    f"父目录: {item.path.parent}\n输入: {item.path}\n输出: {item.output_path}"
                )
        self.refresh_action_states()

    def apply_search(self, text: str) -> None:
        needle = text.strip().lower()
        for row, item in enumerate(self.items):
            search_miss = needle not in item.path.name.lower()
            failure_miss = self.failed_only and item.status != "失败"
            self.table.setRowHidden(row, search_miss or failure_miss)
        self.select_all_btn.setText("全选可见项" if needle or self.failed_only else "全选队列")
        self.select_none_btn.setText("取消可见项" if needle or self.failed_only else "取消选择队列")

    def focus_search(self) -> None:
        self.search_input.setFocus(Qt.ShortcutFocusReason)
        self.search_input.selectAll()

    def show_failed_items(self) -> None:
        self.failed_only = True
        self.apply_search(self.search_input.text())
        self.summary_label.setText("仅显示失败项")

    def update_summary(self) -> None:
        selected = self.selected_items()
        size = sum(item.size for item in selected)
        self.summary_label.setText(f"已选择 {len(selected)} 个 / {format_size(size)}")
        self.refresh_action_states()

    def sync_selection_from_table(self) -> None:
        for row, item in enumerate(self.items):
            table_item = self.table.item(row, 0)
            item.selected = bool(table_item and table_item.checkState() == Qt.Checked)

    def selected_items(self) -> list[VideoItem]:
        self.sync_selection_from_table()
        return [item for item in self.items if item.selected]

    def set_all_checked(self, checked: bool) -> None:
        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            if self.table.isRowHidden(row):
                continue
            table_item = self.table.item(row, 0)
            if table_item:
                table_item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
                self.items[row].selected = checked
        self.table.blockSignals(False)
        self.update_summary()

    def remove_selected_rows(self) -> None:
        selected_rows = sorted(
            {index.row() for index in self.table.selectionModel().selectedRows()},
            reverse=True,
        )
        if not selected_rows:
            return
        for row in selected_rows:
            if 0 <= row < len(self.items):
                self.items.pop(row)
        self.render_table()

    def retry_failed_items(self) -> None:
        found = False
        for item in self.items:
            is_failure = item.status == "失败"
            item.selected = is_failure
            if is_failure:
                item.status = "待压缩"
                item.progress = 0
                found = True
        if not found:
            return
        self.failed_only = False
        self.search_input.clear()
        self.result_bar.hide()
        self.render_table()
        self.start_compression()

    def start_compression(self) -> None:
        if self.worker and self.worker.isRunning():
            return
        selected = self.selected_items()
        if not selected:
            self.refresh_action_states()
            return
        if not self._toolchain_check_complete:
            QMessageBox.information(self, APP_NAME, "压缩工具还在检测，请稍后再试。")
            return
        report = self._toolchain_report
        if report is None:
            QMessageBox.warning(self, APP_NAME, f"压缩工具链检测失败，无法开始。\n{self.ffmpeg_label.toolTip()}")
            return
        if not report.ok:
            QMessageBox.warning(self, APP_NAME, f"压缩工具链不完整，无法开始。\n{self.ffmpeg_label.toolTip()}")
            return
        if self.output_mode_combo.currentData() == "custom" and not self.output_dir_edit.text().strip():
            QMessageBox.information(self, APP_NAME, "请先选择统一输出目录。")
            return
        if self.conflict_combo.currentData() == "overwrite":
            reply = QMessageBox.question(
                self,
                APP_NAME,
                "覆盖模式会替换已存在的输出文件。确定开始吗？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return

        self.result_bar.hide()
        self.failed_only = False
        self.active_batch_rows = []
        for item in selected:
            row = self.items.index(item)
            self.active_batch_rows.append(row)
            item.gui_row = row
            item.status = "等待中"
            item.progress = 0
            item.output_path = self.preview_output_path(item)
        self.completed_in_batch = 0
        self.current_batch_row = None
        self._compression_summary_received = False
        self.cancel_requested = False
        self._reset_pause_ui()
        self._diagnostic_records = []
        self.render_table()
        self.total_progress.setValue(0)
        self.set_controls_enabled(False, running=True)
        worker = CompressionWorker(
            selected,
            self.current_options(),
            prevent_sleep=supports_sleep_inhibition() and self.sleep_switch.isChecked(),
        )
        self.worker = worker
        worker.itemStarted.connect(self.on_item_started)
        worker.itemProgress.connect(self.on_item_progress)
        worker.itemStatus.connect(self.on_item_status)
        worker.batchProgress.connect(self.on_batch_progress)
        worker.finishedSummary.connect(self.on_finished)
        worker.workerError.connect(self.on_worker_error)
        worker.pauseAvailable.connect(self.on_pause_available)
        worker.pauseChanged.connect(self.on_pause_changed)
        worker.workComplete.connect(self.on_worker_work_complete)
        worker.finished.connect(self.on_worker_thread_finished)
        worker.start()

    def cancel_compression(self) -> None:
        if self.worker and self.worker.isRunning():
            self.cancel_requested = True
            self._reset_pause_ui()
            self.summary_label.setText("正在取消当前任务…")
            if self.current_batch_row is not None:
                self.set_row_status(self.current_batch_row, "正在取消", "正在结束 FFmpeg 进程")
            self.worker.cancel()
            self.cancel_btn.setEnabled(False)

    def _reset_pause_ui(self) -> None:
        self._compression_paused = False
        self._pause_available = False
        self.pause_btn.setText("暂停")
        self.pause_btn.setAccessibleName("暂停压缩")
        self.pause_btn.setEnabled(False)

    def toggle_pause(self) -> None:
        worker = self.worker
        if worker is None or not worker.isRunning() or self.cancel_requested:
            return
        self.pause_btn.setEnabled(False)
        if self._compression_paused:
            worker.resume()
        else:
            worker.pause()

    def on_pause_available(self, available: bool) -> None:
        self._pause_available = bool(available)
        running = bool(self.worker and self.worker.isRunning())
        self.pause_btn.setEnabled(
            supports_process_pause()
            and self._pause_available
            and running
            and not self.cancel_requested
        )

    def on_pause_changed(self, paused: bool) -> None:
        was_paused = self._compression_paused
        self._compression_paused = bool(paused)
        self.pause_btn.setText("继续" if paused else "暂停")
        self.pause_btn.setAccessibleName("继续压缩" if paused else "暂停压缩")
        running = bool(self.worker and self.worker.isRunning())
        self.pause_btn.setEnabled(
            supports_process_pause()
            and self._pause_available
            and running
            and not self.cancel_requested
        )
        row = self.current_batch_row
        if row is None or not (0 <= row < len(self.items)) or self.cancel_requested:
            return
        if not paused and not was_paused:
            return
        item = self.items[row]
        if paused:
            self.set_row_status(row, "已暂停", "点击继续后从当前位置恢复")
            self.summary_label.setText(f"已暂停 {item.path.name} · {item.progress}%")
        else:
            self.set_row_status(row, "压缩中", "")
            self.summary_label.setText(f"正在压缩 {item.path.name} · {item.progress}%")

    def on_item_started(self, row: int, total: int) -> None:
        self.current_batch_row = row
        if self.cancel_requested:
            self.set_row_status(row, "正在取消", "正在结束 FFmpeg 进程")
            return
        self.set_row_status(row, "压缩中", "")
        if 0 <= row < len(self.items):
            self.summary_label.setText(f"正在压缩 {self.items[row].path.name}")

    def on_item_progress(self, row: int, percent: int) -> None:
        if not (0 <= row < len(self.items)):
            return
        item = self.items[row]
        item.progress = max(0, min(100, int(percent)))
        widget = self.table.cellWidget(row, 4)
        if isinstance(widget, QProgressBar):
            widget.setValue(item.progress)
        if self.cancel_requested:
            return
        total = len(self.active_batch_rows)
        if total:
            overall = int((self.completed_in_batch + item.progress / 100) / total * 100)
            self.total_progress.setValue(min(100, overall))
        self.summary_label.setText(f"正在压缩 {item.path.name} · {item.progress}%")

    def on_item_status(self, row: int, status: str, message: str) -> None:
        if self.cancel_requested and status not in {"已取消", "完成", "失败", "已跳过", "未变小"}:
            return
        self.set_row_status(row, status, message)

    def set_row_status(self, row: int, status: str, message: str) -> None:
        if 0 <= row < len(self.items):
            self.items[row].status = status
            cell = self.table.item(row, 3)
            if cell:
                signals_were_blocked = self.table.blockSignals(True)
                try:
                    cell.setText(status)
                    cell.setForeground(STATUS_COLORS.get(status, QColor("#334155")))
                    cell.setToolTip(message)
                finally:
                    self.table.blockSignals(signals_were_blocked)

    def on_batch_progress(self, current: int, total: int, success: int, failed: int, skipped: int) -> None:
        self.completed_in_batch = max(self.completed_in_batch, current)
        self.total_progress.setValue(int(current / total * 100) if total else 0)
        if self.cancel_requested:
            return
        current_name = ""
        if self.current_batch_row is not None and 0 <= self.current_batch_row < len(self.items):
            current_name = f" · {self.items[self.current_batch_row].path.name}"
        self.summary_label.setText(
            f"进度 {current}/{total}{current_name} · 成功 {success} · 失败 {failed} · 跳过 {skipped}"
        )

    def classify_result(self, summary: Any) -> tuple[str, str]:
        if summary.cancelled:
            return "cancelled", "任务已取消"
        not_smaller = int(getattr(summary, "not_smaller_count", 0))
        completed = summary.success_count + not_smaller
        if summary.fail_count and (completed or summary.skipped_count):
            return "partial", "部分文件未完成"
        if summary.fail_count:
            return "failure", "压缩失败"
        if summary.skipped_count and not completed:
            return "all_skipped", "全部文件已跳过"
        if not_smaller and not summary.success_count:
            return "not_smaller", "压缩完成，文件未变小"
        if completed:
            return "success", "压缩完成"
        return "failure", "没有生成结果"

    def on_finished(self, summary) -> None:  # type: ignore[no-untyped-def]
        self._compression_summary_received = True
        self._last_summary = summary
        self._diagnostic_records = []
        self._store_summary_diagnostics(summary)
        if summary.cancelled:
            for row in self.active_batch_rows:
                if 0 <= row < len(self.items) and self.items[row].status in {"等待中", "待压缩"}:
                    self.items[row].status = "未执行"
            self.render_table()
        elif self.active_batch_rows:
            self.total_progress.setValue(100)

        self.last_result_dirs = self.result_directories(summary.results)
        if self.last_result_dirs:
            self.last_output_dir = self.last_result_dirs[-1]
        kind, title = self.classify_result(summary)
        not_smaller = int(getattr(summary, "not_smaller_count", 0))
        detail = (
            f"成功 {summary.success_count} · 未变小 {not_smaller} · 跳过 {summary.skipped_count} · "
            f"失败 {summary.fail_count} · 节省 {format_size(summary.saved_size)}"
        )
        if len(summary.results) == 1 and summary.results[0].state == JobState.NOT_SMALLER:
            result = summary.results[0]
            detail += f" · 原 {format_size(result.original_size)} → 现 {format_size(result.output_size)}"
        if summary.cancelled:
            detail = f"已完成 {summary.success_count} 个；未执行项已保留在队列中。"
        self.result_bar.show_result(kind, title, detail, bool(summary.fail_count))
        self.summary_label.setText(title)

    def on_worker_error(self, diagnostic: object) -> None:
        self._reset_pause_ui()
        self._compression_summary_received = False
        filename = ""
        input_path = ""
        output_path = ""
        if self.current_batch_row is not None and 0 <= self.current_batch_row < len(self.items):
            item = self.items[self.current_batch_row]
            filename = item.path.name
            input_path = str(item.path)
            output_path = str(item.output_path or self.preview_output_path(item))
        record = self._store_diagnostic(
            diagnostic,
            phase="compression",
            filename=filename,
            input_path=input_path,
            output_path=output_path,
        )
        message = str(record.get("summary") or "压缩任务异常")
        for row in self.active_batch_rows:
            if 0 <= row < len(self.items) and self.items[row].status in {"等待中", "压缩中"}:
                self.items[row].status = "未执行"
        self.render_table()
        self.result_bar.show_result("failure", "压缩任务异常", message, False, True)
        self.summary_label.setText("压缩任务异常")

    def on_worker_work_complete(self) -> None:
        if not self._close_when_idle:
            self.set_controls_enabled(True)

    def on_worker_thread_finished(self) -> None:
        sender = self.sender()
        if sender is None or sender is self.worker:
            self.worker = None
        self.cancel_requested = False
        self._reset_pause_ui()
        self.refresh_action_states()
        self._finish_deferred_close()

    def output_directories(self) -> list[Path]:
        dirs: list[Path] = []
        seen: set[str] = set()
        if self.last_output_dir:
            dirs.append(self.last_output_dir)
            seen.add(str(self.last_output_dir).lower())
        for item in self.items:
            path = item.output_path or self.preview_output_path(item)
            directory = path.parent
            key = str(directory).lower()
            if key not in seen:
                seen.add(key)
                dirs.append(directory)
        return dirs

    def result_directories(self, results) -> list[Path]:  # type: ignore[no-untyped-def]
        dirs: list[Path] = []
        seen: set[str] = set()
        for result in results:
            if not result.output_path:
                continue
            state = getattr(result, "state", None)
            kept = bool(getattr(result, "success", False) or getattr(result, "skipped", False))
            if not kept and state not in {JobState.SUCCEEDED, JobState.SKIPPED, JobState.NOT_SMALLER}:
                continue
            output_path = Path(result.output_path)
            if not output_path.exists():
                continue
            directory = output_path if output_path.is_dir() else output_path.parent
            key = str(directory).lower()
            if key not in seen:
                seen.add(key)
                dirs.append(directory)
        return dirs

    def output_dir_exists(self, item: VideoItem) -> bool:
        path = item.output_path or self.preview_output_path(item)
        return path.parent.exists()

    def open_output_folder(self) -> None:
        self.open_directories(self.output_directories())

    def open_all_output_folders(self) -> None:
        self.show_directory_menu(self.output_directories(), self.open_all_outputs_btn)

    def open_result_output_folders(self) -> None:
        self.show_directory_menu(self.last_result_dirs, self.result_bar.open_btn)

    def show_directory_menu(self, dirs: list[Path], anchor: QWidget) -> None:
        existing = [directory for directory in dirs if directory.exists()]
        if not existing:
            QMessageBox.information(self, APP_NAME, "还没有生成可打开的结果目录。")
            return
        if len(existing) == 1:
            self.open_directories(existing)
            return
        menu = QMenu(self)
        default_action = QAction(f"打开默认目录 · {existing[0]}", menu)
        default_action.triggered.connect(lambda: self.open_directories([existing[0]]))
        menu.addAction(default_action)
        menu.addSeparator()
        for directory in existing:
            action = QAction(str(directory), menu)
            action.triggered.connect(lambda _checked=False, path=directory: self.open_directories([path]))
            menu.addAction(action)
        self.output_menu = menu
        menu.popup(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def open_directories(self, dirs: list[Path]) -> None:
        for directory in dirs:
            if directory.exists():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))
                return
        QMessageBox.information(self, APP_NAME, "还没有生成可打开的结果目录。")

    def copy_diagnostics(self) -> None:
        payload = {
            "app_version": APP_VERSION,
            "platform": platform.platform(),
            "profile": self.profile_id,
            "output_mode": self.output_mode_combo.currentData() or "source_subfolder",
            "tool_status": self.toolchain_status(),
            "diagnostics": self._diagnostic_records,
        }
        QApplication.clipboard().setText(json.dumps(payload, ensure_ascii=False, indent=2))
        self.summary_label.setText("诊断已复制")

    def toolchain_status(self) -> dict[str, Any]:
        report = self._toolchain_report
        if report is None:
            return {"ready": False, "summary": "not checked"}
        return {
            "ready": bool(report.ok),
            "ffmpeg": bool(report.ffmpeg_available),
            "ffprobe": bool(report.ffprobe_available),
            "libx264": bool(report.libx264),
            "aac": bool(report.aac),
            "zscale": bool(report.zscale),
            "tonemap": bool(report.tonemap),
            "version": report.version,
            "diagnostic": report.diagnostic,
        }

    def _normalize_diagnostic(
        self,
        diagnostic: object,
        phase: str,
        filename: str = "",
        input_path: str = "",
        output_path: str = "",
    ) -> dict[str, Any]:
        if isinstance(diagnostic, dict):
            record = dict(diagnostic)
        else:
            record = {"summary": str(diagnostic)}
        record.setdefault("phase", phase)
        record.setdefault("filename", filename)
        if filename and not record.get("filename"):
            record["filename"] = filename
        record.setdefault("input_path", input_path)
        record.setdefault("output_path", output_path)
        if input_path and not record.get("input_path"):
            record["input_path"] = input_path
        if output_path and not record.get("output_path"):
            record["output_path"] = output_path
        record["input_path"] = str(record.get("input_path") or "")
        record["output_path"] = str(record.get("output_path") or "")
        record.setdefault("error_code", f"{phase}_error")
        record.setdefault("summary", "")
        record.setdefault("traceback", "")
        record["timestamp"] = datetime.now(timezone.utc).isoformat()
        record["app_version"] = APP_VERSION
        record["platform"] = platform.platform()
        record["profile"] = self.profile_id
        record["output_mode"] = self.output_mode_combo.currentData() or "source_subfolder"
        record["tool_status"] = self.toolchain_status()
        return record

    def _store_diagnostic(
        self,
        diagnostic: object,
        phase: str,
        filename: str = "",
        input_path: str = "",
        output_path: str = "",
    ) -> dict[str, Any]:
        record = self._normalize_diagnostic(
            diagnostic,
            phase,
            filename,
            input_path,
            output_path,
        )
        self._diagnostic_records.append(record)
        try:
            directory = _default_log_dir()
            directory.mkdir(parents=True, exist_ok=True)
            with (directory / "gui-diagnostics.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass
        return record

    def _store_summary_diagnostics(self, summary: Any) -> None:
        for index, result in enumerate(summary.results):
            state = getattr(result, "state", None)
            error_code = str(getattr(result, "error_code", "") or "")
            error = str(getattr(result, "error", "") or "")
            detail = str(getattr(result, "diagnostic", "") or "")
            if state != JobState.FAILED and not error_code and not error:
                continue
            filename = ""
            normalized_input_path = ""
            input_info = getattr(result, "input_info", None)
            input_path = getattr(input_info, "path", None)
            if input_path:
                filename = Path(input_path).name
                normalized_input_path = str(Path(input_path))
            elif index < len(self.active_batch_rows):
                row = self.active_batch_rows[index]
                if 0 <= row < len(self.items):
                    item = self.items[row]
                    filename = item.path.name
                    normalized_input_path = str(item.path)
            output_path = getattr(result, "output_path", None)
            self._store_diagnostic(
                {
                    "phase": "compression",
                    "filename": filename,
                    "input_path": normalized_input_path,
                    "output_path": str(Path(output_path)) if output_path else "",
                    "error_code": error_code or "compression_failed",
                    "summary": error or detail or "压缩失败",
                    "traceback": detail,
                },
                phase="compression",
            )

    def open_log_directory(self) -> None:
        directory = _default_log_dir()
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, APP_NAME, f"无法打开日志目录: {exc}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))

    def choose_output_directory(self) -> None:
        start_dir = self.output_dir_edit.text().strip() or self._custom_output_dir
        path = QFileDialog.getExistingDirectory(self, "选择输出文件夹", start_dir)
        if path:
            self._custom_output_dir = path
            custom_index = self.output_mode_combo.findData("custom")
            if custom_index >= 0 and self.output_mode_combo.currentIndex() != custom_index:
                self.output_mode_combo.setCurrentIndex(custom_index)
            self.output_dir_edit.setText(path)
            self.save_output_settings()

    def activate_custom_output(self, text: str) -> None:
        path = text.strip()
        if not path:
            return
        self._custom_output_dir = path
        custom_index = self.output_mode_combo.findData("custom")
        if custom_index >= 0 and self.output_mode_combo.currentIndex() != custom_index:
            self.output_mode_combo.setCurrentIndex(custom_index)
        self.save_output_settings()

    def on_output_directory_changed(self, text: str) -> None:
        if self.output_mode_combo.currentData() == "custom":
            self._custom_output_dir = text.strip()
            self.save_output_settings()
        self.refresh_output_preview()

    def save_output_settings(self) -> None:
        self.settings.setValue(
            "output/mode",
            self.output_mode_combo.currentData() or "source_subfolder",
        )
        self.settings.setValue("output/directory", self._custom_output_dir)

    def update_output_controls(self, *_args: Any) -> None:
        custom = self.output_mode_combo.currentData() == "custom"
        current_text = self.output_dir_edit.text().strip()
        self.output_dir_edit.blockSignals(True)
        try:
            if custom:
                if not current_text and self._custom_output_dir:
                    self.output_dir_edit.setText(self._custom_output_dir)
                self.output_dir_edit.setPlaceholderText("输入或选择统一输出文件夹")
            else:
                if current_text:
                    self._custom_output_dir = current_text
                self.output_dir_edit.clear()
                self.output_dir_edit.setPlaceholderText(
                    "每个源文件旁的 compressed 文件夹（可直接输入修改）"
                )
        finally:
            self.output_dir_edit.blockSignals(False)
        self.output_dir_edit.setEnabled(True)
        self.output_dir_btn.setEnabled(True)
        self.save_output_settings()
        self.refresh_output_preview()

    def toggle_settings_panel(self, checked: bool = False) -> None:
        self._settings_user_visible = bool(checked)
        self.update_settings_visibility(force=True)

    def update_settings_visibility(self, force: bool = False) -> None:
        wide = self.width() >= 860
        visible = self._settings_user_visible if force else (wide and self._settings_user_visible)
        self.settings_scroll.setVisible(visible)
        self.settings_toggle_btn.setChecked(visible)
        self.settings_toggle_btn.setAccessibleDescription(
            "设置面板已展开" if visible else "设置面板已收起"
        )
        self.settings_toggle_btn.setProperty("expanded", visible)

    def is_busy(self) -> bool:
        return bool(
            (self.worker and self.worker.isRunning())
            or (self.scan_worker and self.scan_worker.isRunning())
        )

    def refresh_action_states(self) -> None:
        busy = self.is_busy()
        has_highlight = bool(self.table.selectionModel().selectedRows())
        has_checked = any(item.selected for item in self.items)
        toolchain_ready = bool(self._toolchain_report and self._toolchain_report.ok)
        self.remove_btn.setEnabled(not busy and has_highlight)
        self.start_btn.setEnabled(not busy and has_checked and toolchain_ready)
        self.open_all_outputs_btn.setEnabled(any(self.output_dir_exists(item) for item in self.items))
        self.open_output_btn.setEnabled(bool(self.output_directories()) and not busy)

    def set_controls_enabled(self, enabled: bool, running: bool = False, scanning: bool = False) -> None:
        self._reset_pause_ui()
        for widget in (
            self.add_files_btn,
            self.add_folder_btn,
            self.select_all_btn,
            self.select_none_btn,
            self.open_all_outputs_btn,
            self.open_output_btn,
            self.conflict_combo,
            self.output_mode_combo,
            self.output_dir_edit,
            self.output_dir_btn,
            self.crf_spin,
            self.preset_combo,
            self.audio_combo,
            self.advanced_toggle,
            self.search_input,
            self.settings_toggle_btn,
            self.sleep_switch,
            *self.preset_buttons.buttons(),
        ):
            widget.setEnabled(enabled)
        self.table.setEnabled(enabled)
        self.table.setAcceptDrops(enabled)
        self.queue_panel.setAcceptDrops(enabled)
        self.cancel_btn.setEnabled(running)
        if scanning:
            self.cancel_scan_btn.setEnabled(True)
        self.update_output_controls()
        self.refresh_action_states()
        if running or scanning:
            self.start_btn.setEnabled(False)
            self.remove_btn.setEnabled(False)

    def resizeEvent(self, event):  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        if hasattr(self, "settings_scroll"):
            self.update_settings_visibility()

    def closeEvent(self, event):  # type: ignore[no-untyped-def]
        if self.worker and self.worker.isRunning():
            reply = QMessageBox.question(self, APP_NAME, "任务仍在进行，是否取消并退出？")
            if reply != QMessageBox.Yes:
                event.ignore()
                return
            self._close_when_idle = True
            self._reset_pause_ui()
            self.worker.cancel()
            self.hide()
            event.ignore()
            return
        if self.scan_worker and self.scan_worker.isRunning():
            self._close_when_idle = True
            self.scan_worker.cancel()
            self.hide()
            event.ignore()
            return
        if self.toolchain_worker and self.toolchain_worker.isRunning():
            self._close_when_idle = True
            self.toolchain_worker.requestInterruption()
            self.hide()
            event.ignore()
            return
        super().closeEvent(event)

    def _finish_deferred_close(self) -> None:
        toolchain_busy = bool(self.toolchain_worker and self.toolchain_worker.isRunning())
        if self._close_when_idle and not self.is_busy() and not toolchain_busy:
            self._close_when_idle = False
            QTimer.singleShot(0, self.close)


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("FanTools")
    icon_path = resource_path("assets/app_icon.svg")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
