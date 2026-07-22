from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .core import IdCardOcrError, OcrResult


class OcrInitializationError(IdCardOcrError):
    pass


class OcrRecognitionError(Exception):
    pass


def extract_fields(text: str) -> OcrResult:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    name = ""
    for index, line in enumerate(lines):
        match = re.search(r"姓\s*名\s*[:：]?\s*(.*)$", line)
        if match is None:
            continue
        candidate = match.group(1).strip()
        if candidate:
            name = candidate
        elif index + 1 < len(lines):
            name = lines[index + 1].strip()
        compact_name = "".join(name.split())
        if compact_name.startswith(("性别", "民族", "出生", "住址", "公民身份号码")):
            name = ""
        break

    compact = "".join(text.split())
    match = re.search(r"(?<!\d)\d{17}[0-9Xx](?![0-9Xx])", compact)
    id_number = match.group(0) if match is not None else ""
    return OcrResult(name, id_number)


class MacOSVisionBackend:
    def __init__(self) -> None:
        compiler = shutil.which("clang")
        if compiler is None:
            raise OcrInitializationError("macOS 系统缺少 Clang 编译器，无法启动本地 Vision OCR")

        self._temporary_directory = tempfile.TemporaryDirectory(prefix="idcard-vision-")
        self._executable = Path(self._temporary_directory.name) / "vision_ocr"
        source = Path(__file__).with_name("vision_ocr.m")
        try:
            completed = subprocess.run(
                [
                    compiler,
                    "-fobjc-arc",
                    "-O2",
                    str(source),
                    "-o",
                    str(self._executable),
                    "-framework",
                    "Foundation",
                    "-framework",
                    "Vision",
                    "-framework",
                    "CoreImage",
                    "-framework",
                    "ImageIO",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.close()
            raise OcrInitializationError("无法初始化 macOS Vision OCR") from exc
        if completed.returncode != 0:
            self.close()
            raise OcrInitializationError("无法编译 macOS Vision OCR 组件，请检查 Command Line Tools")

    def recognize(self, path: Path) -> OcrResult:
        try:
            completed = subprocess.run(
                [str(self._executable), str(path)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise OcrRecognitionError("图片处理失败") from exc
        if completed.returncode != 0:
            raise OcrRecognitionError("图片处理失败")
        try:
            payload = json.loads(completed.stdout)
            text = payload["text"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise OcrRecognitionError("图片处理失败") from exc
        return extract_fields(text)

    def close(self) -> None:
        temporary_directory = getattr(self, "_temporary_directory", None)
        if temporary_directory is not None:
            temporary_directory.cleanup()
            self._temporary_directory = None


class TesseractBackend:
    def __init__(self) -> None:
        self._executable = shutil.which("tesseract")
        if self._executable is None:
            raise OcrInitializationError("系统未安装 Tesseract，无法启动本地 OCR")
        completed = subprocess.run(
            [self._executable, "--list-langs"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=30,
            check=False,
        )
        languages = set(completed.stdout.split())
        if completed.returncode != 0 or "chi_sim" not in languages or "eng" not in languages:
            raise OcrInitializationError("Tesseract 必须安装 chi_sim 和 eng 本地语言包")

    def recognize(self, path: Path) -> OcrResult:
        try:
            completed = subprocess.run(
                [self._executable, str(path), "stdout", "-l", "chi_sim+eng", "--psm", "1"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise OcrRecognitionError("图片处理失败") from exc
        if completed.returncode != 0:
            raise OcrRecognitionError("图片处理失败")
        return extract_fields(completed.stdout)

    def close(self) -> None:
        return None


def create_ocr_backend():
    configured = os.environ.get("IDCARD_OCR_BACKEND", "").strip().lower()
    if configured == "tesseract":
        return TesseractBackend()
    if configured and configured != "vision":
        raise OcrInitializationError("IDCARD_OCR_BACKEND 只支持 vision 或 tesseract")
    if platform.system() == "Darwin":
        return MacOSVisionBackend()
    if configured == "vision":
        raise OcrInitializationError("Vision OCR 仅支持 macOS")
    return TesseractBackend()
