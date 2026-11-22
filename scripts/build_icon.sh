#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
SVG_PATH="$PROJECT_DIR/src/ableton_audio_collector/assets/app_icon.svg"
ICON_OUTPUT="$PROJECT_DIR/src/ableton_audio_collector/assets/app_icon.icns"
ICONSET_DIR="$PROJECT_DIR/build/AbletonInspector.iconset"
PREVIEW_DIR="$PROJECT_DIR/build/icon-preview"
PREVIEW_PATH="$PREVIEW_DIR/app_icon.svg.png"

mkdir -p "$ICONSET_DIR" "$PREVIEW_DIR"
qlmanage -t -s 1024 -o "$PREVIEW_DIR" "$SVG_PATH" >/dev/null

sips -z 16 16 "$PREVIEW_PATH" --out "$ICONSET_DIR/icon_16x16.png" >/dev/null
sips -z 32 32 "$PREVIEW_PATH" --out "$ICONSET_DIR/icon_16x16@2x.png" >/dev/null
sips -z 32 32 "$PREVIEW_PATH" --out "$ICONSET_DIR/icon_32x32.png" >/dev/null
sips -z 64 64 "$PREVIEW_PATH" --out "$ICONSET_DIR/icon_32x32@2x.png" >/dev/null
sips -z 128 128 "$PREVIEW_PATH" --out "$ICONSET_DIR/icon_128x128.png" >/dev/null
sips -z 256 256 "$PREVIEW_PATH" --out "$ICONSET_DIR/icon_128x128@2x.png" >/dev/null
sips -z 256 256 "$PREVIEW_PATH" --out "$ICONSET_DIR/icon_256x256.png" >/dev/null
sips -z 512 512 "$PREVIEW_PATH" --out "$ICONSET_DIR/icon_256x256@2x.png" >/dev/null
sips -z 512 512 "$PREVIEW_PATH" --out "$ICONSET_DIR/icon_512x512.png" >/dev/null
cp "$PREVIEW_PATH" "$ICONSET_DIR/icon_512x512@2x.png"

iconutil -c icns "$ICONSET_DIR" -o "$ICON_OUTPUT"
echo "Created $ICON_OUTPUT"
