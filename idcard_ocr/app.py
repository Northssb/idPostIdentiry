from __future__ import annotations

import queue
import sys
import threading
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Sequence

from .core import (
    BatchProcessor,
    BatchSummary,
    IdCardOcrError,
    InputPathError,
    export_xlsx,
    resolve_output_path,
    scan_images,
)
from .ocr import create_ocr_backend


@dataclass(frozen=True)
class DesktopJobResult:
    target: Path
    summary: BatchSummary


def run_recognition_job(
    input_dir: Path,
    output_dir: Path,
    output_name: str | None,
    overwrite: bool,
    backend_factory: Callable[[], object] = create_ocr_backend,
    progress: Callable[[int, int, str], None] | None = None,
) -> DesktopJobResult:
    input_dir = Path(input_dir)
    images = scan_images(input_dir)
    if not images:
        raise InputPathError("输入文件夹中没有 JPG、JPEG 或 PNG 图片")
    target = resolve_output_path(Path(output_dir), output_name, overwrite)

    backend = backend_factory()
    try:
        rows, summary = BatchProcessor(input_dir, backend, progress).process(images)
    finally:
        close = getattr(backend, "close", None)
        if callable(close):
            close()

    export_xlsx(target, rows)
    return DesktopJobResult(target, summary)


class IdCardOcrApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("身份证批量识别")
        self.root.geometry("720x460")
        self.root.minsize(640, 420)

        self.input_dir = tk.StringVar()
        self.output_dir = tk.StringVar()
        self.output_name = tk.StringVar(value="身份证识别结果.xlsx")
        self.overwrite = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="请选择身份证图片文件夹和Excel导出文件夹")
        self.progress_text = tk.StringVar(value="等待开始")
        self._events: queue.Queue[tuple[str, object]] = queue.Queue()
        self._running = False

        self._configure_style()
        self._build_layout()
        self.root.after(100, self._poll_events)

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        if "aqua" in style.theme_names():
            style.theme_use("aqua")
        style.configure("Title.TLabel", font=("Arial", 22, "bold"))
        style.configure("Hint.TLabel", foreground="#5F6368")
        style.configure("Status.TLabel", foreground="#1F2937")
        style.configure("TButton", padding=(12, 7))

    def _build_layout(self) -> None:
        container = ttk.Frame(self.root, padding=(28, 24, 28, 24))
        container.pack(fill="both", expand=True)
        container.columnconfigure(1, weight=1)

        ttk.Label(container, text="身份证批量识别", style="Title.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w"
        )
        ttk.Label(
            container,
            text="本地离线识别身份证正面图片，导出身份证号码和姓名。",
            style="Hint.TLabel",
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 24))

        self._add_folder_row(container, 2, "图片文件夹", self.input_dir, self._choose_input)
        self._add_folder_row(container, 3, "导出文件夹", self.output_dir, self._choose_output)

        ttk.Label(container, text="Excel文件名").grid(row=4, column=0, sticky="w", pady=10)
        self.output_name_entry = ttk.Entry(container, textvariable=self.output_name)
        self.output_name_entry.grid(row=4, column=1, columnspan=2, sticky="ew", padx=(14, 0), pady=10)

        self.overwrite_check = ttk.Checkbutton(
            container,
            text="允许覆盖同名Excel文件",
            variable=self.overwrite,
        )
        self.overwrite_check.grid(row=5, column=1, columnspan=2, sticky="w", padx=(14, 0), pady=(2, 18))

        self.progress = ttk.Progressbar(container, mode="determinate", maximum=100)
        self.progress.grid(row=6, column=0, columnspan=3, sticky="ew")
        ttk.Label(container, textvariable=self.progress_text, style="Hint.TLabel").grid(
            row=7, column=0, columnspan=3, sticky="w", pady=(7, 16)
        )

        status_frame = ttk.Frame(container, padding=(14, 12))
        status_frame.grid(row=8, column=0, columnspan=3, sticky="nsew")
        status_frame.columnconfigure(0, weight=1)
        ttk.Label(
            status_frame,
            textvariable=self.status,
            style="Status.TLabel",
            wraplength=620,
        ).grid(row=0, column=0, sticky="w")

        container.rowconfigure(8, weight=1)
        self.start_button = ttk.Button(container, text="开始识别", command=self._start)
        self.start_button.grid(row=9, column=2, sticky="e", pady=(20, 0))

    def _add_folder_row(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        variable: tk.StringVar,
        command: Callable[[], None],
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=10)
        entry = ttk.Entry(parent, textvariable=variable)
        entry.grid(row=row, column=1, sticky="ew", padx=(14, 10), pady=10)
        button = ttk.Button(parent, text="选择…", command=command)
        button.grid(row=row, column=2, sticky="e", pady=10)

    def _choose_input(self) -> None:
        selected = filedialog.askdirectory(title="选择身份证图片文件夹")
        if selected:
            self.input_dir.set(selected)

    def _choose_output(self) -> None:
        selected = filedialog.askdirectory(title="选择Excel导出文件夹")
        if selected:
            self.output_dir.set(selected)

    def _start(self) -> None:
        if self._running:
            return
        input_value = self.input_dir.get().strip()
        output_value = self.output_dir.get().strip()
        if not input_value or not output_value:
            messagebox.showwarning("请选择文件夹", "请先选择身份证图片文件夹和Excel导出文件夹。")
            return

        self._running = True
        self.start_button.configure(state="disabled")
        self.progress.configure(value=0)
        self.progress_text.set("正在初始化本地OCR…")
        self.status.set("识别过程中请勿关闭应用。")
        thread = threading.Thread(
            target=self._run_worker,
            args=(
                Path(input_value),
                Path(output_value),
                self.output_name.get().strip() or None,
                self.overwrite.get(),
            ),
            daemon=True,
        )
        thread.start()

    def _run_worker(
        self,
        input_dir: Path,
        output_dir: Path,
        output_name: str | None,
        overwrite: bool,
    ) -> None:
        def report(current: int, total: int, status: str) -> None:
            self._events.put(("progress", (current, total, status)))

        try:
            result = run_recognition_job(
                input_dir,
                output_dir,
                output_name,
                overwrite,
                progress=report,
            )
            self._events.put(("completed", result))
        except IdCardOcrError as exc:
            self._events.put(("error", str(exc)))
        except Exception:
            self._events.put(("error", "程序运行失败，未生成有效结果"))

    def _poll_events(self) -> None:
        try:
            while True:
                event, payload = self._events.get_nowait()
                if event == "progress":
                    current, total, status = payload
                    self.progress.configure(value=current / total * 100)
                    category = "成功"
                    if str(status).startswith("失败"):
                        category = "失败"
                    elif str(status).startswith("重复"):
                        category = "重复"
                    self.progress_text.set(f"正在处理 {current}/{total} · {category}")
                elif event == "completed":
                    self._finish(payload)
                elif event == "error":
                    self._fail(str(payload))
        except queue.Empty:
            pass
        self.root.after(100, self._poll_events)

    def _finish(self, result: DesktopJobResult) -> None:
        self._running = False
        self.start_button.configure(state="normal")
        self.progress.configure(value=100)
        summary = result.summary
        self.progress_text.set("处理完成")
        self.status.set(
            f"扫描 {summary.scanned} 张，成功 {summary.succeeded} 张，"
            f"失败 {summary.failed} 张，重复 {summary.duplicated} 张。\n"
            f"Excel已保存到：{result.target}"
        )
        messagebox.showinfo("识别完成", self.status.get())

    def _fail(self, message: str) -> None:
        self._running = False
        self.start_button.configure(state="normal")
        self.progress.configure(value=0)
        self.progress_text.set("处理失败")
        self.status.set(message)
        messagebox.showerror("识别失败", message)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments == ["--self-test"]:
        backend = create_ocr_backend()
        try:
            return 0
        finally:
            close = getattr(backend, "close", None)
            if callable(close):
                close()
    if len(arguments) == 3 and arguments[0] == "--self-test":
        result = run_recognition_job(
            Path(arguments[1]),
            Path(arguments[2]),
            "身份证识别自检.xlsx",
            True,
        )
        return 0 if result.summary.succeeded > 0 else 1

    root = tk.Tk()
    IdCardOcrApp(root)
    root.mainloop()
    return 0
