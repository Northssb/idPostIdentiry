"""本地离线身份证识别工具。"""

from .core import BatchProcessor, OcrResult, export_xlsx, scan_images

__all__ = ["BatchProcessor", "OcrResult", "export_xlsx", "scan_images"]
