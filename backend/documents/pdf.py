"""Local PDF text extraction and per-page OCR with retained geometry and quality."""

import math
import threading
from difflib import SequenceMatcher

import numpy as np

from backend.core.config import settings
from backend.documents.cleaning import (ExtractedPage, ProcessingError, TextBlock, clean_text,
                               reconstruct_text, text_is_broken, useful_length)

_pdf_lock = threading.Lock()  # PDFium calls across documents must also be serialized.
_ocr_lock = threading.Lock()
_engine = None


def _normalized_bbox(bounds, page) -> tuple[float, float, float, float]:
    """Map PDF coordinates through crop offsets and clockwise page rotation."""
    width, height = page.get_size()
    crop_x, crop_y, crop_right, crop_top = page.get_bbox()
    base_width, base_height = crop_right - crop_x, crop_top - crop_y
    rotation = page.get_rotation()
    x0, y0, x1, y1 = bounds
    points = []
    for x, y in ((x0, y0), (x0, y1), (x1, y0), (x1, y1)):
        x, y = x - crop_x, y - crop_y
        if rotation == 90:
            x, y = y, x
        elif rotation == 180:
            x, y = base_width - x, y
        elif rotation == 270:
            x, y = base_height - y, base_width - x
        else:
            y = base_height - y
        points.append((x / width, y / height))
    return (min(p[0] for p in points), min(p[1] for p in points),
            max(p[0] for p in points), max(p[1] for p in points))


def _pdf_info(path: str) -> int:
    import pypdfium2 as pdfium
    try:
        with _pdf_lock, pdfium.PdfDocument(path) as pdf:
            count = len(pdf)
    except Exception as exc:
        raise ProcessingError("PDF 无法打开，请确认文件完整且已解除密码保护") from exc
    if count > settings.max_pdf_pages:
        raise ProcessingError(f"PDF 超过 {settings.max_pdf_pages} 页，请拆分后上传")
    return count


def _inspect_page(path: str, number: int) -> tuple[str, list[TextBlock], float]:
    import pypdfium2 as pdfium
    with _pdf_lock, pdfium.PdfDocument(path) as pdf:
        page = pdf[number]
        try:
            width, height = page.get_size()
            if not all(math.isfinite(v) and v > 0 for v in (width, height)):
                raise ProcessingError("PDF 页面尺寸无效")
            textpage = page.get_textpage()
            try:
                native = textpage.get_text_range()
                blocks = []
                for index in range(textpage.count_rects()):
                    x0, y0, x1, y1 = textpage.get_rect(index)
                    text = textpage.get_text_bounded(x0, y0, x1, y1)
                    if text.strip():
                        blocks.append(TextBlock(text, _normalized_bbox((x0, y0, x1, y1), page)))
            finally:
                textpage.close()
            image_area = 0.0
            for obj in page.get_objects(filter=[pdfium.raw.FPDF_PAGEOBJ_IMAGE]):
                x0, y0, x1, y1 = _normalized_bbox(obj.get_bounds(), page)
                image_area += max(0, min(1, x1) - max(0, x0)) * max(0, min(1, y1) - max(0, y0))
            return native, blocks, min(1.0, image_area)
        finally:
            page.close()


def _render_page(path: str, number: int, dpi: int) -> np.ndarray:
    import pypdfium2 as pdfium
    with _pdf_lock, pdfium.PdfDocument(path) as pdf:
        page = pdf[number]
        try:
            width, height = page.get_size()
            scale = min(dpi / 72, settings.pdf_ocr_max_side / max(width, height))
            bitmap = page.render(scale=scale)
            try:
                return bitmap.to_numpy().copy()  # outlive native memory; BGR is RapidOCR's ndarray format
            finally:
                bitmap.close()
        finally:
            page.close()


def _recognize(pixels: np.ndarray) -> list[TextBlock]:
    global _engine
    with _ocr_lock:
        if _engine is None:
            from rapidocr import RapidOCR
            # Retain low-score results for quality review, rather than silently dropping them.
            _engine = RapidOCR(params={"Global.text_score": 0.0,
                                      "Global.max_side_len": settings.pdf_ocr_max_side,
                                      "EngineConfig.onnxruntime.intra_op_num_threads": 2})
        result = _engine(pixels)
        if result.txts is None:
            return []
        height, width = pixels.shape[:2]
        blocks = []
        for text, box, score in zip(result.txts, result.boxes, result.scores):
            if text.strip():
                blocks.append(TextBlock(text, (float(min(box[:, 0])) / width, float(min(box[:, 1])) / height,
                                              float(max(box[:, 0])) / width, float(max(box[:, 1])) / height),
                                        float(score), "ocr"))
        return blocks


def _quality(blocks: list[TextBlock]) -> float:
    weight = sum(max(1, len(b.text)) for b in blocks)
    return sum((b.score or 0) * max(1, len(b.text)) for b in blocks) / max(1, weight)


def _same_block(a: TextBlock, b: TextBlock) -> bool:
    if not a.bbox or not b.bbox:
        return False
    ax0, ay0, ax1, ay1 = a.bbox
    bx0, by0, bx1, by1 = b.bbox
    intersection = max(0, min(ax1, bx1) - max(ax0, bx0)) * max(0, min(ay1, by1) - max(ay0, by0))
    area = min(max(0, ax1 - ax0) * max(0, ay1 - ay0), max(0, bx1 - bx0) * max(0, by1 - by0))
    return (intersection / max(area, 1e-9) > 0.5
            and SequenceMatcher(None, "".join(a.text.split()), "".join(b.text.split())).ratio() > 0.80)


def extract_pdf(path: str) -> list[ExtractedPage]:
    if settings.pdf_ocr_mode not in {"auto", "always", "off"}:
        raise ProcessingError("PDF_OCR_MODE 必须为 auto、always 或 off")
    pages = []
    for number in range(_pdf_info(path)):
        try:
            try:
                native, native_blocks, image_area = _inspect_page(path, number)
            except Exception:
                if settings.pdf_ocr_mode == "off":
                    raise
                native, native_blocks, image_area = "", [], 1.0
            page = ExtractedPage(number, native, native_text=native, blocks=native_blocks)
            broken = text_is_broken(native)
            needs_ocr = (settings.pdf_ocr_mode == "always" or
                         (settings.pdf_ocr_mode == "auto" and
                          (useful_length(native) < settings.pdf_ocr_min_text_chars or broken or image_area >= 0.25)))
            if not needs_ocr:
                if native_blocks:
                    page.raw_text = reconstruct_text(native_blocks)
                if not native.strip() and image_area:
                    page.status = "skipped"
                    page.warnings.append("OCR 已关闭，扫描内容未入库")
                pages.append(page)
                continue
            try:
                pixels = _render_page(path, number, settings.pdf_ocr_dpi)
                ocr = _recognize(pixels)
                # One bounded retry for low-quality or missing recognition; rotation is handled by the engine.
                if (not ocr or _quality(ocr) < settings.pdf_ocr_accept_score) and settings.pdf_ocr_retry_dpi > settings.pdf_ocr_dpi:
                    retry_pixels = _render_page(path, number, settings.pdf_ocr_retry_dpi)
                    retry = _recognize(retry_pixels)
                    if _quality(retry) > _quality(ocr):
                        ocr, pixels = retry, retry_pixels
                page.ocr_text = "\n".join(b.text for b in ocr)
                # Always retain OCR boxes, including low-quality and rejected results, for review.
                page.blocks = native_blocks + ocr
                if ocr and _quality(ocr) >= settings.pdf_ocr_accept_score:
                    kept = [] if broken else (list(native_blocks) or ([TextBlock(native)] if native.strip() else []))
                    kept.extend(b for b in ocr if not any(_same_block(b, n) for n in kept))
                    page.raw_text = reconstruct_text(kept, join_chinese_lines=True)
                    page.selected_blocks = kept
                    page.extraction = "mixed" if native_blocks and not broken else "ocr"
                    if any((b.score or 0) < settings.pdf_ocr_warning_score for b in ocr):
                        page.warnings.append("部分 OCR 文字置信度较低，请核对数字、型号等内容")
                elif native.strip() and not broken:
                    page.incomplete = image_area > 0
                    page.warnings.append("OCR 失败，图片区域未入库" if page.incomplete else "OCR 补充识别失败，已保留原生文字")
                elif not ocr and float(np.mean(pixels[:, :, :3] < 245)) < 0.001:
                    page.status = "blank"
                else:
                    page.status = "skipped"
                    page.warnings.append("页面有内容，但 OCR 结果不可靠，未入库")
            except Exception:
                if native.strip() and not broken:
                    page.incomplete = image_area > 0
                    page.warnings.append("OCR 失败，图片区域未入库" if page.incomplete else "OCR 补充识别失败，已保留原生文字")
                else:
                    page.status = "failed"
                    page.warnings.append("OCR 识别失败，请检查本地依赖与模型后重试")
            pages.append(page)
        except Exception:
            pages.append(ExtractedPage(number, "", status="failed", warnings=["页面解析失败，请重试或替换文件"]))
    return pages
