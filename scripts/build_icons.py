#!/usr/bin/env python3
"""Render the source SVG into cross-platform application icon assets."""

from __future__ import annotations

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ASSET_ROOT = PROJECT_ROOT / "src" / "ableton_audio_collector" / "assets"
SVG_PATH = ASSET_ROOT / "app_icon.svg"
PNG_PATH = ASSET_ROOT / "app_icon.png"
ICO_PATH = ASSET_ROOT / "app_icon.ico"


def main() -> int:
    renderer = QSvgRenderer(str(SVG_PATH))
    if not renderer.isValid():
        print(f"Could not load icon source: {SVG_PATH}", file=sys.stderr)
        return 1

    image = QImage(1024, 1024, QImage.Format.Format_ARGB32)
    image.fill(QColor(0, 0, 0, 0))
    painter = QPainter(image)
    renderer.render(painter, QRectF(0, 0, 1024, 1024))
    painter.end()

    if not image.save(str(PNG_PATH), "PNG"):
        print(f"Could not write PNG icon: {PNG_PATH}", file=sys.stderr)
        return 1

    ico_image = image.scaled(
        256,
        256,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    png_data = _png_bytes(ico_image)
    ICO_PATH.write_bytes(_ico_with_embedded_png(png_data))
    print(f"Created {PNG_PATH}")
    print(f"Created {ICO_PATH}")
    return 0


def _png_bytes(image: QImage) -> bytes:
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise RuntimeError("Qt could not encode the application icon as PNG.")
    buffer.close()
    return bytes(data)


def _ico_with_embedded_png(png_data: bytes) -> bytes:
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack(
        "<BBBBHHII",
        0,
        0,
        0,
        0,
        1,
        32,
        len(png_data),
        6 + 16,
    )
    return header + entry + png_data


if __name__ == "__main__":
    raise SystemExit(main())
