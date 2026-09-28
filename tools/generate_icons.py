# -*- coding: utf-8 -*-
"""Generate platform icon files from the project SVG using Qt itself."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QColor, QImage, QImageWriter, QPainter
from PySide6.QtSvg import QSvgRenderer


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assets" / "app_icon.svg"


def render(size: int) -> QImage:
    renderer = QSvgRenderer(str(SOURCE))
    if not renderer.isValid():
        raise RuntimeError(f"Invalid SVG icon: {SOURCE}")
    image = QImage(QSize(size, size), QImage.Format_ARGB32)
    image.fill(QColor(Qt.transparent))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    renderer.render(painter)
    painter.end()
    return image


def write(image: QImage, path: Path, fmt: bytes) -> None:
    writer = QImageWriter(str(path), QByteArray(fmt))
    if not writer.write(image):
        raise RuntimeError(f"Could not write {path}: {writer.errorString()}")


def main() -> int:
    assets = ROOT / "assets"
    assets.mkdir(exist_ok=True)
    image = render(1024)
    write(image, assets / "app_icon.png", b"png")
    write(image, assets / "app_icon.ico", b"ico")
    if b"icns" in [bytes(fmt) for fmt in QImageWriter.supportedImageFormats()]:
        write(image, assets / "app_icon.icns", b"icns")
    print("Generated app_icon.png, app_icon.ico and available platform formats.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
