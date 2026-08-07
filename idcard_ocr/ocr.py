from __future__ import annotations

import csv
import ctypes
import ctypes.util
import functools
import io
import json
import os
import platform
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

from .core import (
    IdCardOcrError,
    OcrResult,
    normalize_id_number,
    normalize_name,
    validate_id_number,
    validate_name,
)


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
        name = re.split(r"性\s*别|民\s*族|出\s*生|住\s*址|公民身份号码", name, maxsplit=1)[0].strip()
        compact_name = "".join(name.split())
        if compact_name.startswith(("性别", "民族", "出生", "住址", "公民身份号码")):
            name = ""
        break

    compact = "".join(text.split())
    match = re.search(r"(?<!\d)\d{17}[0-9Xx](?![0-9Xx])", compact)
    id_number = match.group(0) if match is not None else ""
    return OcrResult(name, id_number)


def _is_usable(result: OcrResult) -> bool:
    name = normalize_name(result.name)
    id_number = normalize_id_number(result.id_number)
    return validate_name(name) and validate_id_number(id_number)


def _extract_tesseract_tsv(text: str) -> OcrResult:
    grouped_words: dict[tuple[str, str, str, str], list[tuple[str, float]]] = {}
    for record in csv.DictReader(io.StringIO(text), delimiter="\t"):
        word = (record.get("text") or "").strip()
        if record.get("level") != "5" or not word:
            continue
        key = (
            record.get("page_num", ""),
            record.get("block_num", ""),
            record.get("par_num", ""),
            record.get("line_num", ""),
        )
        try:
            confidence = float(record.get("conf", "-1"))
        except ValueError:
            confidence = -1.0
        grouped_words.setdefault(key, []).append((word, confidence))

    lines = ["".join(word for word, _ in words) for words in grouped_words.values()]
    result = extract_fields("\n".join(lines))
    if not result.name:
        return result

    for words in grouped_words.values():
        line = "".join(word for word, _ in words)
        name_start = line.find("姓名")
        if name_start < 0:
            continue
        name_start += len("姓名")
        name_end = len(line)
        for label in ("性别", "民族", "出生", "住址", "公民身份号码"):
            position = line.find(label, name_start)
            if position >= 0:
                name_end = min(name_end, position)
        offset = 0
        confidences: list[float] = []
        for word, confidence in words:
            word_end = offset + len(word)
            if word_end > name_start and offset < name_end:
                confidences.append(confidence)
            offset = word_end
        if confidences and min(confidences) < 35.0:
            return OcrResult("", result.id_number)
        break
    return result


def _read_exif_orientation(path: Path) -> int:
    if path.suffix.lower() not in {".jpg", ".jpeg"}:
        return 1
    try:
        with path.open("rb") as image:
            data = image.read(262_144)
    except OSError:
        return 1
    if not data.startswith(b"\xff\xd8"):
        return 1

    offset = 2
    while offset + 4 <= len(data):
        if data[offset] != 0xFF:
            break
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            break
        marker = data[offset]
        offset += 1
        if marker in {0xD8, 0xD9}:
            continue
        if marker == 0xDA or offset + 2 > len(data):
            break
        segment_length = struct.unpack_from(">H", data, offset)[0]
        if segment_length < 2 or offset + segment_length > len(data):
            break
        payload = data[offset + 2 : offset + segment_length]
        offset += segment_length
        if marker != 0xE1 or not payload.startswith(b"Exif\x00\x00"):
            continue

        tiff = payload[6:]
        if len(tiff) < 8:
            return 1
        if tiff[:2] == b"II":
            byte_order = "<"
        elif tiff[:2] == b"MM":
            byte_order = ">"
        else:
            return 1
        if struct.unpack_from(f"{byte_order}H", tiff, 2)[0] != 42:
            return 1
        directory_offset = struct.unpack_from(f"{byte_order}I", tiff, 4)[0]
        if directory_offset + 2 > len(tiff):
            return 1
        entry_count = struct.unpack_from(f"{byte_order}H", tiff, directory_offset)[0]
        for index in range(entry_count):
            entry_offset = directory_offset + 2 + index * 12
            if entry_offset + 12 > len(tiff):
                return 1
            tag, value_type, value_count = struct.unpack_from(
                f"{byte_order}HHI", tiff, entry_offset
            )
            if tag == 0x0112 and value_type == 3 and value_count >= 1:
                orientation = struct.unpack_from(
                    f"{byte_order}H", tiff, entry_offset + 8
                )[0]
                return orientation if 1 <= orientation <= 8 else 1
        return 1
    return 1


def _scale_factor(width: int, height: int, max_dimension: int) -> float:
    longest_edge = max(width, height)
    if longest_edge <= max_dimension:
        return 1.0
    return max_dimension / longest_edge


def _leptonica_candidates(tesseract_executable: str) -> list[str]:
    candidates: list[str] = []
    library_name = ctypes.util.find_library("lept")
    if library_name:
        candidates.append(library_name)
    executable_directory = Path(tesseract_executable).resolve().parent
    for pattern in ("*lept*.dll", "liblept*.so*", "liblept*.dylib"):
        candidates.extend(str(path) for path in sorted(executable_directory.glob(pattern)))
    return list(dict.fromkeys(candidates))


@functools.lru_cache(maxsize=4)
def _load_leptonica(tesseract_executable: str):
    library = None
    for candidate in _leptonica_candidates(tesseract_executable):
        try:
            library = ctypes.CDLL(candidate)
            break
        except OSError:
            continue
    if library is None:
        raise OcrRecognitionError("图片方向处理失败")

    library.pixReadMem.argtypes = [ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t]
    library.pixReadMem.restype = ctypes.c_void_p
    library.pixRotateOrth.argtypes = [ctypes.c_void_p, ctypes.c_int]
    library.pixRotateOrth.restype = ctypes.c_void_p
    library.pixFlipLR.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    library.pixFlipLR.restype = ctypes.c_void_p
    library.pixFlipTB.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    library.pixFlipTB.restype = ctypes.c_void_p
    library.pixGetWidth.argtypes = [ctypes.c_void_p]
    library.pixGetWidth.restype = ctypes.c_int
    library.pixGetHeight.argtypes = [ctypes.c_void_p]
    library.pixGetHeight.restype = ctypes.c_int
    library.pixScale.argtypes = [ctypes.c_void_p, ctypes.c_float, ctypes.c_float]
    library.pixScale.restype = ctypes.c_void_p
    library.pixWriteMemPng.argtypes = [
        ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte)),
        ctypes.POINTER(ctypes.c_size_t),
        ctypes.c_void_p,
        ctypes.c_float,
    ]
    library.pixWriteMemPng.restype = ctypes.c_int
    library.pixDestroy.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
    library.pixDestroy.restype = None
    library.lept_free.argtypes = [ctypes.c_void_p]
    library.lept_free.restype = None
    return library


def _prepare_tesseract_image(
    path: Path,
    tesseract_executable: str,
    max_dimension: int,
    rotation: int = 0,
) -> bytes | None:
    orientation = _read_exif_orientation(path)
    try:
        source_data = path.read_bytes()
    except OSError as exc:
        raise OcrRecognitionError("图片方向处理失败") from exc

    library = _load_leptonica(tesseract_executable)
    source_buffer = (ctypes.c_ubyte * len(source_data)).from_buffer_copy(source_data)
    source = library.pixReadMem(source_buffer, len(source_data))
    if not source:
        raise OcrRecognitionError("图片方向处理失败")

    created: list[ctypes.c_void_p] = []
    try:
        working = source
        if orientation != 1:
            if orientation == 2:
                transformed = library.pixFlipLR(None, source)
            elif orientation == 3:
                transformed = library.pixRotateOrth(source, 2)
            elif orientation == 4:
                transformed = library.pixFlipTB(None, source)
            elif orientation in {5, 7}:
                rotated = library.pixRotateOrth(source, 1)
                if not rotated:
                    raise OcrRecognitionError("图片方向处理失败")
                created.append(ctypes.c_void_p(rotated))
                transform = library.pixFlipLR if orientation == 5 else library.pixFlipTB
                transformed = transform(None, rotated)
            elif orientation == 6:
                transformed = library.pixRotateOrth(source, 1)
            else:
                transformed = library.pixRotateOrth(source, 3)
            if not transformed:
                raise OcrRecognitionError("图片方向处理失败")
            created.append(ctypes.c_void_p(transformed))
            working = transformed

        if rotation:
            rotated = library.pixRotateOrth(working, rotation)
            if not rotated:
                raise OcrRecognitionError("图片方向处理失败")
            created.append(ctypes.c_void_p(rotated))
            working = rotated

        factor = _scale_factor(
            library.pixGetWidth(working),
            library.pixGetHeight(working),
            max_dimension,
        )
        if factor < 1.0:
            scaled = library.pixScale(
                working, ctypes.c_float(factor), ctypes.c_float(factor)
            )
            if not scaled:
                raise OcrRecognitionError("图片方向处理失败")
            created.append(ctypes.c_void_p(scaled))
            working = scaled

        if orientation == 1 and rotation == 0 and factor == 1.0:
            return None

        output_pointer = ctypes.POINTER(ctypes.c_ubyte)()
        output_size = ctypes.c_size_t()
        status = library.pixWriteMemPng(
            ctypes.byref(output_pointer),
            ctypes.byref(output_size),
            working,
            ctypes.c_float(0.0),
        )
        if status != 0 or not output_pointer or output_size.value == 0:
            raise OcrRecognitionError("图片方向处理失败")
        try:
            return ctypes.string_at(output_pointer, output_size.value)
        finally:
            library.lept_free(ctypes.cast(output_pointer, ctypes.c_void_p))
    finally:
        for image in reversed(created):
            library.pixDestroy(ctypes.byref(image))
        source_pointer = ctypes.c_void_p(source)
        library.pixDestroy(ctypes.byref(source_pointer))


class MacOSVisionBackend:
    def __init__(self) -> None:
        bundle_root = getattr(sys, "_MEIPASS", None)
        bundled_executable = Path(bundle_root) / "vision_ocr" if bundle_root else None
        if bundled_executable is not None and bundled_executable.is_file():
            self._temporary_directory = None
            self._executable = bundled_executable
            return

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

    def _invoke(self, path: Path, enhanced: bool) -> OcrResult:
        command = [str(self._executable), str(path)]
        if enhanced:
            command.append("--enhanced")
        try:
            completed = subprocess.run(
                command,
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
        result = extract_fields(text)
        return OcrResult(result.name, result.id_number, "enhanced" if enhanced else "standard")

    def recognize(self, path: Path) -> OcrResult:
        standard_result: OcrResult | None = None
        try:
            raw_standard = self._invoke(path, False)
            standard_result = OcrResult(raw_standard.name, raw_standard.id_number, "standard")
            if _is_usable(standard_result):
                return standard_result
        except OcrRecognitionError:
            pass
        try:
            enhanced_result = self._invoke(path, True)
            return OcrResult(enhanced_result.name, enhanced_result.id_number, "enhanced")
        except OcrRecognitionError:
            if standard_result is not None:
                return standard_result
            raise

    def close(self) -> None:
        temporary_directory = getattr(self, "_temporary_directory", None)
        if temporary_directory is not None:
            temporary_directory.cleanup()
            self._temporary_directory = None


class TesseractBackend:
    def __init__(self) -> None:
        self._environment = os.environ.copy()
        bundle_root = getattr(sys, "_MEIPASS", None)
        bundled_directory = Path(bundle_root) / "tesseract" if bundle_root else None
        bundled_executable = (
            bundled_directory / "tesseract.exe" if bundled_directory is not None else None
        )
        if bundled_executable is not None and bundled_executable.is_file():
            self._executable = str(bundled_executable)
            tessdata = bundled_directory / "tessdata"
            if tessdata.is_dir():
                self._environment["TESSDATA_PREFIX"] = str(tessdata)
        else:
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
            env=self._environment,
        )
        languages = set(completed.stdout.split())
        if (
            completed.returncode != 0
            or "chi_sim" not in languages
            or "osd" not in languages
        ):
            raise OcrInitializationError("Tesseract 必须安装 chi_sim 和 osd 本地语言包")

    def _invoke(
        self,
        path: Path,
        page_mode: str,
        strategy: str,
        max_dimension: int,
        rotation: int = 0,
    ) -> OcrResult:
        prepared_image = _prepare_tesseract_image(
            path, self._executable, max_dimension, rotation
        )
        input_path = "stdin" if prepared_image is not None else str(path)
        command = [
            self._executable,
            input_path,
            "stdout",
            "-l",
            "chi_sim",
            "--psm",
            page_mode,
            "tsv",
        ]
        run_arguments = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "timeout": 60,
            "check": False,
            "env": self._environment,
        }
        if prepared_image is None:
            run_arguments["stdin"] = subprocess.DEVNULL
        else:
            run_arguments["input"] = prepared_image
        try:
            completed = subprocess.run(command, **run_arguments)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise OcrRecognitionError("图片处理失败") from exc
        if completed.returncode != 0:
            raise OcrRecognitionError("图片处理失败")
        output = completed.stdout
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        result = _extract_tesseract_tsv(output)
        return OcrResult(result.name, result.id_number, strategy)

    def recognize(self, path: Path) -> OcrResult:
        fallback_result: OcrResult | None = None
        attempts = (
            ("6", "standard", 1024, 0),
            ("1", "enhanced", 1024, 0),
            ("6", "enhanced", 1280, 0),
            ("1", "enhanced", 1280, 0),
            ("6", "enhanced", 1024, 2),
            ("1", "enhanced", 1024, 2),
        )
        for page_mode, strategy, max_dimension, rotation in attempts:
            try:
                raw = self._invoke(
                    path, page_mode, strategy, max_dimension, rotation
                )
            except OcrRecognitionError:
                continue
            result = OcrResult(raw.name, raw.id_number, strategy)
            if fallback_result is None or sum(
                bool(value) for value in (result.name, result.id_number)
            ) > sum(
                bool(value)
                for value in (fallback_result.name, fallback_result.id_number)
            ):
                fallback_result = result
            if _is_usable(result):
                return result
        if fallback_result is not None:
            return fallback_result
        raise OcrRecognitionError("图片处理失败")

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
