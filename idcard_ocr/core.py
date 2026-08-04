from __future__ import annotations

import os
import re
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Iterable, Protocol
from xml.sax.saxutils import escape


SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png"}
MAX_BATCH_SIZE = 100


class IdCardOcrError(Exception):
    """可安全展示给命令行用户的业务错误。"""


class InputPathError(IdCardOcrError):
    pass


class BatchLimitError(IdCardOcrError):
    pass


class OutputPathError(IdCardOcrError):
    pass


@dataclass(frozen=True)
class OcrResult:
    name: str
    id_number: str
    strategy: str = "standard"


class OcrBackend(Protocol):
    def recognize(self, path: Path) -> OcrResult:
        ...


@dataclass(frozen=True)
class ResultRow:
    source: str
    name: str
    id_number: str
    status: str

    def as_tuple(self) -> tuple[str, str, str, str]:
        return self.source, self.name, self.id_number, self.status


@dataclass(frozen=True)
class BatchSummary:
    scanned: int
    succeeded: int
    retried: int
    failed: int
    duplicated: int

    def as_tuple(self) -> tuple[int, int, int, int, int]:
        return self.scanned, self.succeeded, self.retried, self.failed, self.duplicated


def normalize_name(value: str) -> str:
    return value.strip()


def validate_name(value: str) -> bool:
    character = r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]"
    return re.fullmatch(rf"{character}+(?:·{character}+)*", value) is not None


def normalize_id_number(value: str) -> str:
    compact = "".join(value.split())
    if compact.endswith("x"):
        compact = compact[:-1] + "X"
    return compact


def validate_id_number(value: str) -> bool:
    if re.fullmatch(r"\d{17}[\dX]", value) is None:
        return False
    try:
        date.fromisoformat(f"{value[6:10]}-{value[10:12]}-{value[12:14]}")
    except ValueError:
        return False
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    check_codes = "10X98765432"
    total = sum(int(character) * weight for character, weight in zip(value[:17], weights))
    return value[-1] == check_codes[total % 11]


def scan_images(input_dir: Path) -> list[Path]:
    input_dir = Path(input_dir)
    if not input_dir.exists():
        raise InputPathError("输入文件夹不存在")
    if not input_dir.is_dir():
        raise InputPathError("输入路径不是文件夹")
    if not os.access(input_dir, os.R_OK):
        raise InputPathError("输入文件夹不可读")

    images: list[Path] = []
    for current_root, directories, files in os.walk(input_dir, followlinks=False):
        root = Path(current_root)
        directories[:] = sorted(
            (name for name in directories if not (root / name).is_symlink()),
            key=str.casefold,
        )
        for filename in sorted(files, key=str.casefold):
            path = root / filename
            if path.is_symlink() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            images.append(path)
            if len(images) > MAX_BATCH_SIZE:
                raise BatchLimitError(f"受支持图片超过{MAX_BATCH_SIZE}张")

    images.sort(key=lambda path: path.relative_to(input_dir).as_posix().casefold())
    return images


def resolve_output_path(
    output_dir: Path,
    output_name: str | None,
    overwrite: bool,
    now: datetime | None = None,
) -> Path:
    output_dir = Path(output_dir)
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OutputPathError("无法创建导出文件夹") from exc
    if not output_dir.is_dir():
        raise OutputPathError("导出路径不是文件夹")
    if not os.access(output_dir, os.W_OK):
        raise OutputPathError("导出文件夹不可写")

    if output_name is None:
        stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
        output_name = f"身份证识别结果_{stamp}.xlsx"
    if (
        not output_name
        or output_name in {".", ".."}
        or "/" in output_name
        or "\\" in output_name
        or not output_name.lower().endswith(".xlsx")
    ):
        raise OutputPathError("输出文件名必须是不含路径的 .xlsx 文件名")

    target = output_dir / output_name
    if target.exists() and not overwrite:
        raise OutputPathError("输出文件已存在；如需覆盖请使用 --overwrite")
    return target


class BatchProcessor:
    def __init__(
        self,
        input_dir: Path,
        ocr: OcrBackend,
        progress: Callable[[int, int, str], None] | None = None,
    ) -> None:
        self.input_dir = Path(input_dir)
        self.ocr = ocr
        self.progress = progress

    def process(self, images: Iterable[Path]) -> tuple[list[ResultRow], BatchSummary]:
        paths = list(images)
        rows: list[ResultRow] = []
        accepted: dict[str, tuple[str, str]] = {}
        succeeded = 0
        retried = 0
        failed = 0
        duplicated = 0

        for index, path in enumerate(paths, start=1):
            source = path.relative_to(self.input_dir).as_posix()
            try:
                raw = self.ocr.recognize(path)
                name = normalize_name(raw.name)
                id_number = normalize_id_number(raw.id_number)
                if not name:
                    row = ResultRow(source, "", "", "失败：未识别到姓名")
                    failed += 1
                elif not validate_name(name):
                    row = ResultRow(source, "", "", "失败：姓名格式无效")
                    failed += 1
                elif not id_number:
                    row = ResultRow(source, "", "", "失败：未识别到身份证号")
                    failed += 1
                elif not validate_id_number(id_number):
                    row = ResultRow(source, "", "", "失败：身份证号校验失败")
                    failed += 1
                elif id_number in accepted:
                    first_name, first_source = accepted[id_number]
                    status = (
                        f"重复，已保留：{first_source}"
                        if name == first_name
                        else f"重复且姓名冲突，已保留：{first_source}"
                    )
                    row = ResultRow(source, "", "", status)
                    duplicated += 1
                else:
                    accepted[id_number] = (name, source)
                    if raw.strategy == "enhanced":
                        row = ResultRow(source, name, id_number, "成功（增强重试）")
                        retried += 1
                    else:
                        row = ResultRow(source, name, id_number, "成功")
                    succeeded += 1
            except Exception:
                row = ResultRow(source, "", "", "失败：图片处理失败")
                failed += 1

            rows.append(row)
            if self.progress is not None:
                self.progress(index, len(paths), row.status)

        summary = BatchSummary(len(paths), succeeded, retried, failed, duplicated)
        return rows, summary


def _inline_cell(reference: str, value: str, style: int | None = None) -> str:
    style_attribute = f' s="{style}"' if style is not None else ""
    return (
        f'<c r="{reference}" t="inlineStr"{style_attribute}>'
        f'<is><t xml:space="preserve">{escape(value)}</t></is></c>'
    )


def _worksheet_xml(rows: Iterable[object]) -> bytes:
    headers = ("身份证号码", "姓名")
    sheet_rows = [
        '<row r="1">'
        + "".join(_inline_cell(f"{column}1", value, 1) for column, value in zip("AB", headers))
        + "</row>"
    ]
    last_row = 1
    for last_row, row in enumerate(rows, start=2):
        _, name, id_number, _ = row.as_tuple()
        values = (id_number, name)
        sheet_rows.append(
            f'<row r="{last_row}">'
            + "".join(
                _inline_cell(
                    f"{column}{last_row}",
                    str(value),
                    2 if column == "A" else None,
                )
                for column, value in zip("AB", values)
            )
            + "</row>"
        )
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<dimension ref="A1:B{last_row}"/>'
        '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" '
        'activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
        '<cols><col min="1" max="1" width="24" customWidth="1"/>'
        '<col min="2" max="2" width="18" customWidth="1"/>'
        '</cols>'
        '<sheetData>' + "".join(sheet_rows) + '</sheetData>'
        f'<autoFilter ref="A1:B{last_row}"/>'
        '</worksheet>'
    )
    return xml.encode("utf-8")


def export_xlsx(target: Path, rows: Iterable[object]) -> None:
    target = Path(target)
    result_rows = list(rows)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{target.stem}.", suffix=".tmp", dir=target.parent, delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)

        with zipfile.ZipFile(temporary_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "[Content_Types].xml",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                '<Default Extension="xml" ContentType="application/xml"/>'
                '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                '</Types>',
            )
            archive.writestr(
                "_rels/.rels",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                '</Relationships>',
            )
            archive.writestr(
                "xl/workbook.xml",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                '<sheets><sheet name="识别结果" sheetId="1" r:id="rId1"/></sheets></workbook>',
            )
            archive.writestr(
                "xl/_rels/workbook.xml.rels",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
                '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                '</Relationships>',
            )
            archive.writestr("xl/worksheets/sheet1.xml", _worksheet_xml(result_rows))
            archive.writestr(
                "xl/styles.xml",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
                '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
                '<fills count="2"><fill><patternFill patternType="none"/></fill>'
                '<fill><patternFill patternType="gray125"/></fill></fills>'
                '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
                '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
                '<cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
                '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
                '<xf numFmtId="49" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs>'
                '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
                '</styleSheet>',
            )
        os.replace(temporary_path, target)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
