"""Read stored spreadsheet values and retain the cell ranges used for indexing."""

import json
import os
import subprocess
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, time
from functools import lru_cache
from itertools import groupby
from pathlib import Path
from threading import BoundedSemaphore
from zipfile import ZipFile

from defusedxml.ElementTree import iterparse
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.worksheet._reader import DATA_TAG, ROW_TAG, WorkSheetParser

from backend.documents.cleaning import ExtractedPage, ProcessingError

SPREADSHEET_SUFFIXES = {".xls", ".xlsx"}
MAX_ROW = 1_048_576
MAX_COLUMN = 16_384
ROW_LIMIT = 40
COLUMN_LIMIT = 20
PREVIEW_CELL_LIMIT = 256
MAX_INDEX_CELLS = 80_000
MAX_INDEX_CHARS = 2_000_000
MAX_PAGE_CHARS = 4_000
_preview_slots = BoundedSemaphore(2)


class SpreadsheetPreviewBusy(ProcessingError):
    """The preview can be retried when a reader slot becomes available."""


class UnknownSheet(LookupError):
    """The requested worksheet does not exist."""


@dataclass
class SpreadsheetPage(ExtractedPage):
    spreadsheet: dict[str, str | int] = field(default_factory=dict)


@contextmanager
def _xlsx_book(path: Path):
    with ZipFile(path) as archive:
        entries = archive.infolist()
        # Read-only mode still loads shared strings and workbook metadata into memory.
        if (len(entries) > 2048 or sum(entry.file_size for entry in entries) > 64 * 1024 * 1024
                or any(entry.file_size > 8 * 1024 * 1024 for entry in entries
                       if entry.filename.rsplit("/", 1)[-1] in {"sharedStrings.xml", "styles.xml"})):
            raise ProcessingError("Excel 文件解压后过大，请拆分文件")
    book = load_workbook(path, read_only=True, data_only=True, keep_links=False)
    try:
        if len(book.worksheets) > 256 or any(len(sheet.title) > 128 for sheet in book.worksheets):
            raise ProcessingError("Excel 工作表数量或名称超出支持范围")
        yield book
    finally:
        book.close()


def _xlsx_cells(sheet):
    # Openpyxl's rectangular iterator fills missing cells and trusts <dimension>.
    # Use its read-only row parser to keep sparse sheets sparse and preserve value types.
    book = sheet.parent
    with sheet._get_source() as source:
        parser = WorkSheetParser(source, sheet._shared_strings, data_only=True, epoch=book.epoch,
                                 date_formats=book._date_formats, timedelta_formats=book._timedelta_formats)
        sheet_data = None
        last_row = 0
        for event, element in iterparse(source, events=("start", "end")):
            if event == "start" and element.tag == DATA_TAG:
                sheet_data = element
            elif event == "end" and element.tag == DATA_TAG:
                break
            elif event == "end" and element.tag == ROW_TAG and sheet_data is not None:
                row, cells = parser.parse_row(element)
                sheet_data.remove(element)
                parser.row_dimensions.clear()
                if not last_row < row <= MAX_ROW:
                    raise ProcessingError("Excel 单元格坐标无效")
                last_row = row
                previous_column = 0
                for cell in sorted(cells, key=lambda cell: cell["column"]):
                    column, value = cell["column"], cell["value"]
                    if cell["row"] != row or not previous_column < column <= MAX_COLUMN:
                        raise ProcessingError("Excel 单元格坐标无效")
                    previous_column = column
                    if value is None or value == "":
                        continue
                    if isinstance(value, bool):
                        text = "TRUE" if value else "FALSE"
                    elif isinstance(value, (date, time)):
                        text = value.isoformat()
                    else:
                        text = str(value)
                    yield row, column, text


def _run_sheetjs(path: Path, mode: str, *options):
    script = Path(__file__).with_name("sheetjs") / "read_xls.cjs"
    if not (script.parent / "node_modules" / "xlsx" / "package.json").is_file():
        raise ProcessingError("服务端未安装 SheetJS，暂时无法读取 XLS 文件")
    env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP")
           if key in os.environ}
    try:
        result = subprocess.run(["node", "--max-old-space-size=96", str(script), str(path), mode,
                                 *map(str, options)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                env=env, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except FileNotFoundError as exc:
        raise ProcessingError("服务端未安装 Node.js，暂时无法读取 XLS 文件") from exc
    except subprocess.TimeoutExpired as exc:
        raise ProcessingError("XLS 读取超时，请拆分文件后重试") from exc
    if result.returncode == 3:
        raise ProcessingError("Excel 内容过大，请拆分文件后重试")
    if result.returncode == 4:
        raise UnknownSheet
    if result.returncode:
        raise ProcessingError("无法打开 XLS 文件，请确认文件完整且已解除密码保护")
    return json.loads(result.stdout)


@lru_cache(maxsize=128)
def _xlsx_sheet_sizes(path: Path, modified_ns: int, size_bytes: int) -> dict[str, tuple[int, int]]:
    # Original storage keys change on replacement. Retain only the dimensions, never cell data.
    return {}


def _preview_values(path: Path, requested: str | None, row: int, column: int):
    if path.suffix.lower() == ".xls":
        return _run_sheetjs(path, "preview", requested or "", row, column,
                            ROW_LIMIT, COLUMN_LIMIT, PREVIEW_CELL_LIMIT)
    with _xlsx_book(path) as book:
        names = [sheet.title for sheet in book.worksheets]
        if not names:
            raise ProcessingError("Excel 文件没有可预览的工作表")
        name = requested if requested is not None else names[0]
        if name not in names:
            raise UnknownSheet
        stat = path.stat()
        sizes = _xlsx_sheet_sizes(path, stat.st_mtime_ns, stat.st_size)
        known_size = sizes.get(name)
        total_rows, total_columns = known_size or (0, 0)
        page = []
        for r, c, value in _xlsx_cells(book[name]):
            if known_size is None:
                total_rows, total_columns = max(total_rows, r), max(total_columns, c)
            elif r >= row + ROW_LIMIT:
                break
            if row <= r < row + ROW_LIMIT and column <= c < column + COLUMN_LIMIT:
                text = value if len(value) <= PREVIEW_CELL_LIMIT else value[:PREVIEW_CELL_LIMIT - 1] + "…"
                page.append((r, c, text))
        sizes[name] = total_rows, total_columns
        return names, name, total_rows, total_columns, page


def preview_spreadsheet(path: Path, sheet: str | None, row: int, column: int) -> dict:
    if not _preview_slots.acquire(blocking=False):
        raise SpreadsheetPreviewBusy("Excel 预览繁忙，请稍后重试")
    try:
        names, name, total_rows, total_columns, page = _preview_values(path, sheet, row, column)
    except (ProcessingError, UnknownSheet):
        raise
    except Exception as exc:
        raise ProcessingError("无法打开 Excel 文件，请确认文件完整且已解除密码保护") from exc
    finally:
        _preview_slots.release()
    end_row = min(row + ROW_LIMIT, total_rows + 1)
    end_column = min(column + COLUMN_LIMIT, total_columns + 1)
    values = {(r, c): value for r, c, value in page}
    return {"sheets": names, "sheet": name, "row_start": row, "column_start": column,
            "total_rows": total_rows, "total_columns": total_columns,
            "columns": [get_column_letter(c) for c in range(column, end_column)],
            "rows": [[values.get((r, c)) for c in range(column, end_column)] for r in range(row, end_row)],
            "row_limit": ROW_LIMIT, "column_limit": COLUMN_LIMIT}


def _extracted_page(number: int, name: str, cells: list) -> SpreadsheetPage:
    rows, columns, lines = zip(*cells)
    first = f"{get_column_letter(min(columns))}{min(rows)}"
    last = f"{get_column_letter(max(columns))}{max(rows)}"
    cell_range = first if first == last else f"{first}:{last}"
    text = f"{name} {cell_range}\n" + "\n".join(lines)
    return SpreadsheetPage(number, text, spreadsheet={"sheet": name, "range": cell_range,
                                                       "row": min(rows), "column": min(columns)})


def _index_worksheets(worksheets) -> list[ExtractedPage]:
    pages = []
    count = length = 0
    for name, cells in worksheets:
        for _, row_band in groupby(cells, key=lambda cell: (cell[0] - 1) // ROW_LIMIT):
            column_bands = defaultdict(list)
            for row, column, value in row_band:
                count += 1
                length += len(value)
                if count > MAX_INDEX_CELLS or length > MAX_INDEX_CHARS:
                    raise ProcessingError("Excel 可检索内容过大，请拆分文件后上传")
                line = f"{get_column_letter(column)}{row}: {value}"
                column_bands[(column - 1) // COLUMN_LIMIT].append((row, column, line))
            for band in sorted(column_bands):
                batch = []
                size = 0
                for cell in column_bands[band]:
                    if batch and size + len(cell[2]) > MAX_PAGE_CHARS:
                        pages.append(_extracted_page(len(pages), name, batch))
                        batch, size = [], 0
                    batch.append(cell)
                    size += len(cell[2]) + 1
                pages.append(_extracted_page(len(pages), name, batch))
    return pages


def extract_spreadsheet(path: Path) -> list[ExtractedPage]:
    try:
        if path.suffix.lower() == ".xls":
            return _index_worksheets(_run_sheetjs(path, "index", MAX_INDEX_CELLS, MAX_INDEX_CHARS))
        with _xlsx_book(path) as book:
            return _index_worksheets((sheet.title, _xlsx_cells(sheet)) for sheet in book.worksheets)
    except ProcessingError:
        raise
    except Exception as exc:
        raise ProcessingError("无法读取 Excel 文件，请确认文件完整且已解除密码保护") from exc
