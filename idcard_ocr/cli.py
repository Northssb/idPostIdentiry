from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable, Sequence

from .core import (
    BatchProcessor,
    IdCardOcrError,
    InputPathError,
    export_xlsx,
    resolve_output_path,
    scan_images,
)
from .ocr import create_ocr_backend


class CommandArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise InputPathError(f"命令行参数错误：{message}")


def build_parser() -> argparse.ArgumentParser:
    parser = CommandArgumentParser(description="本地离线批量识别身份证正面图片并导出 Excel")
    parser.add_argument("--input-dir", required=True, type=Path, help="身份证图片根文件夹")
    parser.add_argument("--output-dir", required=True, type=Path, help="Excel 导出文件夹")
    parser.add_argument("--output-name", help="可选的 .xlsx 文件名")
    parser.add_argument("--overwrite", action="store_true", help="允许覆盖已存在的输出文件")
    return parser


def _print_progress(current: int, total: int, status: str) -> None:
    category = "成功"
    if status.startswith("失败"):
        category = "失败"
    elif status.startswith("重复"):
        category = "重复"
    print(f"[{current}/{total}] {category}")


def main(
    argv: Sequence[str] | None = None,
    backend_factory: Callable[[], object] = create_ocr_backend,
) -> int:
    parser = build_parser()
    try:
        arguments = parser.parse_args(argv)
        images = scan_images(arguments.input_dir)
        if not images:
            raise InputPathError("输入文件夹中没有 JPG、JPEG 或 PNG 图片")
        target = resolve_output_path(
            arguments.output_dir,
            arguments.output_name,
            arguments.overwrite,
        )

        backend = backend_factory()
        try:
            rows, summary = BatchProcessor(arguments.input_dir, backend, _print_progress).process(images)
        finally:
            close = getattr(backend, "close", None)
            if callable(close):
                close()

        export_xlsx(target, rows)
        print(
            f"处理完成：扫描 {summary.scanned}，成功 {summary.succeeded}，"
            f"失败 {summary.failed}，重复 {summary.duplicated}"
        )
        print(f"Excel 已导出：{target}")
        return 2 if summary.failed or summary.duplicated else 0
    except IdCardOcrError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    except Exception:
        print("错误：程序运行失败，未生成有效结果", file=sys.stderr)
        return 1
