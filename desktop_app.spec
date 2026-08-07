# -*- mode: python ; coding: utf-8 -*-

import sys
from pathlib import Path

project_root = Path(SPECPATH)
binaries = []
datas = []

if sys.platform == "darwin":
    binaries.append((str(project_root / "build_assets" / "vision_ocr"), "."))
elif sys.platform == "win32":
    datas.append((str(project_root / "build_assets" / "tesseract"), "tesseract"))

a = Analysis(
    ["id_card_ocr_app.pyw"],
    pathex=[str(project_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="身份证识别",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
collection = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="身份证识别",
)

if sys.platform == "darwin":
    app = BUNDLE(
        collection,
        name="身份证识别.app",
        icon=None,
        bundle_identifier="com.local.idcardocr",
    )
