#!/bin/sh

set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYTHON=${PYTHON:-/usr/bin/python3}

cd "$PROJECT_ROOT"
"$PYTHON" -c 'import tkinter; print("Tk", tkinter.TkVersion)'
"$PYTHON" -m venv .build-venv-macos
. .build-venv-macos/bin/activate
python -m pip install --upgrade "pyinstaller>=6,<7"

mkdir -p build_assets
clang -fobjc-arc -O2 idcard_ocr/vision_ocr.m \
  -o build_assets/vision_ocr \
  -framework Foundation \
  -framework Vision \
  -framework CoreImage \
  -framework ImageIO

python -m PyInstaller --noconfirm --clean desktop_app.spec
codesign --force --deep --sign - "dist/身份证识别.app"

echo "构建完成：$PROJECT_ROOT/dist/身份证识别.app"
