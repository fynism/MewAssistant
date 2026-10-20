"""Extraction records and conservative cleaning before hierarchical chunking."""

import math
import re
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass, field

PIPELINE_VERSION = "cleaning-v1"


class ProcessingError(ValueError):
    """A fixed, content-free message that can be displayed to the uploader."""


@dataclass
class TextBlock:
    text: str
    bbox: tuple[float, float, float, float] | None = None  # normalized, top-left origin
    score: float | None = None
    source: str = "text"


@dataclass
class ExtractedPage:
    page_number: int
    raw_text: str
    cleaned_text: str = ""
    native_text: str = ""
    ocr_text: str = ""
    blocks: list[TextBlock] = field(default_factory=list)
    selected_blocks: list[TextBlock] = field(default_factory=list)
    extraction: str = "text"
    status: str = "ready"
    warnings: list[str] = field(default_factory=list)
    incomplete: bool = False

    def record(self) -> dict:
        return asdict(self)


class LoadedChunks(list):
    """Keep the loader's list interface while carrying per-document diagnostics."""

    def __init__(self, chunks: list[dict], pages: list[ExtractedPage]):
        super().__init__(chunks)
        self.pages = pages
        self.report = summarize_pages(pages)


def summarize_pages(pages: list[ExtractedPage]) -> dict:
    return {
        "pipeline_version": PIPELINE_VERSION,
        "total_pages": len(pages),
        "indexed_pages": sum(p.status == "ready" and bool(p.cleaned_text) for p in pages),
        "blank_pages": sum(p.status == "blank" for p in pages),
        "incomplete_pages": [p.page_number + 1 for p in pages
                             if p.status in {"failed", "skipped"} or p.incomplete],
        "warnings": [{"page": p.page_number + 1, "messages": p.warnings} for p in pages if p.warnings],
    }


def public_report(report: dict) -> dict:
    """Keep repeated retrieval hits bounded; full diagnostics stay on the document endpoint."""
    keys = ("pipeline_version", "total_pages", "indexed_pages", "blank_pages", "incomplete_pages")
    result = {key: report[key] for key in keys if key in report}
    warnings = report.get("warnings", [])
    result["warnings"] = warnings[:10]
    if len(warnings) > 10:
        result["warning_pages"] = len(warnings)
        result["warnings_truncated"] = True
    return result


def decode_text(data: bytes) -> str:
    """Strict decoding for UTF-8, BOM UTF-16/32, and GB18030 TXT files."""
    if data.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        encodings = ("utf-32",)
    elif data.startswith((b"\xff\xfe", b"\xfe\xff")):
        encodings = ("utf-16",)
    else:
        encodings = ("utf-8-sig", "gb18030")
    for encoding in encodings:
        try:
            text = data.decode(encoding)
        except UnicodeError:
            continue
        controls = sum(unicodedata.category(c) == "Cc" and c not in "\n\r\t\f" for c in text)
        if "\x00" in text or controls > max(1, len(text) * 0.01):
            raise ProcessingError("TXT 包含二进制内容，请上传纯文本文件")
        return text
    raise ProcessingError("TXT 编码无法识别，请另存为 UTF-8 后上传")


def clean_text(text: str) -> str:
    """Preserve punctuation, quantities, indentation, and table column spacing."""
    text = unicodedata.normalize("NFC", text or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n\n")
    text = text.replace("\u00a0", " ").replace("\u202f", " ")
    text = text.replace("\ufeff", "").replace("\u200b", "")
    text = "".join(c for c in text if c in "\n\t" or unicodedata.category(c) != "Cc")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    # Strip surrounding empty lines without erasing the first line's indentation.
    return re.sub(r"\n{3,}", "\n\n", text).strip("\n")


def useful_length(text: str) -> int:
    return sum(c.isalnum() for c in text)


def text_is_broken(text: str) -> bool:
    return text.count("\ufffd") / max(1, len(text)) > 0.05


def _ordered_blocks(blocks: list[TextBlock]) -> list[TextBlock]:
    """Column-first ordering for simple two-column pages, keeping spanning headings."""
    positioned = [b for b in blocks if b.bbox and clean_text(b.text).strip()]
    if len(positioned) != len(blocks):
        return blocks
    left = [b for b in blocks if b.bbox[2] <= 0.48]
    right = [b for b in blocks if b.bbox[0] >= 0.52]
    if len(left) < 3 or len(right) < 3:
        return sorted(blocks, key=lambda b: (round(b.bbox[1], 2), b.bbox[0]))
    # A clear gutter is required; centered lines and ordinary paragraphs don't qualify.
    gutter = min(b.bbox[0] for b in right) - max(b.bbox[2] for b in left)
    if gutter < 0.025:
        return sorted(blocks, key=lambda b: (round(b.bbox[1], 2), b.bbox[0]))
    spanning = [b for b in blocks if b not in left and b not in right]
    result, remaining = [], list(blocks)
    for heading in sorted(spanning, key=lambda b: b.bbox[1]):
        section = [b for b in remaining if b is not heading and b.bbox[3] <= heading.bbox[1]]
        result.extend(sorted((b for b in section if b in left), key=lambda b: b.bbox[1]))
        result.extend(sorted((b for b in section if b in right), key=lambda b: b.bbox[1]))
        result.append(heading)
        remaining = [b for b in remaining if b not in section and b is not heading]
    result.extend(sorted((b for b in remaining if b in left), key=lambda b: b.bbox[1]))
    result.extend(sorted((b for b in remaining if b in right), key=lambda b: b.bbox[1]))
    return result


def reconstruct_text(blocks: list[TextBlock], join_chinese_lines: bool = False) -> str:
    ordered = _ordered_blocks(blocks)
    lines = []
    previous = None
    for block in ordered:
        text = clean_text(block.text).strip()
        if not text:
            continue
        join = False
        if join_chinese_lines and previous and previous.bbox and block.bbox and lines:
            height = max(0.001, previous.bbox[3] - previous.bbox[1])
            gap = block.bbox[1] - previous.bbox[3]
            # Join only aligned, adjacent Chinese prose; retain lists, headings and columns.
            join = (len(lines[-1]) >= 18 and len(text) >= 12
                    and re.search(r"[\u4e00-\u9fff]$", lines[-1]) is not None
                    and re.match(r"^[\u4e00-\u9fff]", text) is not None
                    and not re.match(r"^(?:第.{1,8}[章节条]|[一二三四五六七八九十]+[、.．])", text)
                    and abs(previous.bbox[0] - block.bbox[0]) < 0.02
                    and -0.1 * height <= gap <= height * 0.7)
        if join:
            lines[-1] += text
        else:
            lines.append(text)
        previous = block
    return "\n".join(lines)


def clean_pages(pages: list[ExtractedPage], remove_margins: bool = False) -> None:
    for page in pages:
        page.cleaned_text = clean_text(page.raw_text)
        if text_is_broken(page.cleaned_text):
            page.warnings.append("提取文本含较多乱码，请复核")
        if not page.cleaned_text.strip() and page.status == "ready":
            page.status = "blank"
    if not remove_margins or len(pages) < 3:
        return
    def margin_key(block: TextBlock):
        text = clean_text(block.text).strip()
        if re.fullmatch(r"(?:第\s*)?\d+\s*(?:页)?", text):
            text = "<page-number>"
        side = "top" if block.bbox[3] < 0.10 else "bottom" if block.bbox[1] > 0.90 else None
        return (side, text) if side and text and len(text) <= 120 else None
    counts = Counter(k for page in pages for k in {
        margin_key(b) for b in (page.selected_blocks or page.blocks) if b.bbox} if k)
    repeated = {k for k, n in counts.items() if n >= max(3, math.ceil(len(pages) * 0.8))}
    for page in pages:
        selected = page.selected_blocks or page.blocks
        kept = [b for b in selected if not b.bbox or margin_key(b) not in repeated]
        if len(kept) != len(selected) and kept and page.status == "ready":
            page.cleaned_text = clean_text(reconstruct_text(kept, join_chinese_lines=page.extraction != "text"))
