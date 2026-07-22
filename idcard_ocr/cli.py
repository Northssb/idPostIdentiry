from __future__ import annotations

import argparse
import platform
import subprocess
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
    if status == "成功（增强重试）":
        category = status
    elif status.startswith("失败"):
        category = "失败"
    elif status.startswith("重复"):
        category = "重复"
    print(f"[{current}/{total}] {category}")


def _choose_macos_folder(prompt: str) -> Path:
    completed = subprocess.run(
        ["/usr/bin/osascript", "-e", f'POSIX path of (choose folder with prompt "{prompt}")'],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=300,
        check=False,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        raise InputPathError("已取消文件夹选择")
    return Path(completed.stdout.strip())


def select_directories() -> tuple[Path, Path]:
    if platform.system() == "Darwin" and Path("/usr/bin/osascript").is_file():
        input_dir = _choose_macos_folder("请选择身份证图片文件夹")
        output_dir = _choose_macos_folder("请选择 Excel 导出文件夹")
        return input_dir, output_dir

    try:
        input_value = input("请输入身份证图片文件夹：").strip()
        output_value = input("请输入 Excel 导出文件夹：").strip()
    except EOFError as exc:
        raise InputPathError("当前环境无法交互选择文件夹，请使用命令行参数") from exc
    if not input_value or not output_value:
        raise InputPathError("输入和导出文件夹不能为空")
    return Path(input_value), Path(output_value)


def main(
    argv: Sequence[str] | None = None,
    backend_factory: Callable[[], object] = create_ocr_backend,
    directory_selector: Callable[[], tuple[Path, Path]] = select_directories,
) -> int:
    parser = build_parser()
    try:
        argument_list = list(sys.argv[1:] if argv is None else argv)
        if not argument_list:
            print("未提供命令行参数，请选择输入和导出文件夹。")
            input_dir, output_dir = directory_selector()
            argument_list = ["--input-dir", str(input_dir), "--output-dir", str(output_dir)]
        arguments = parser.parse_args(argument_list)
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
            f"其中增强重试成功 {summary.retried}，失败 {summary.failed}，重复 {summary.duplicated}"
        )
        print(f"Excel 已导出：{target}")
        return 2 if summary.failed or summary.duplicated else 0
    except IdCardOcrError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    except Exception:
        print("错误：程序运行失败，未生成有效结果", file=sys.stderr)
        return 1
