import shutil
import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from openpyxl import Workbook

from backend.documents.cleaning import ProcessingError
from backend.documents.spreadsheet import extract_spreadsheet, preview_spreadsheet

SHEETJS = Path(__file__).resolve().parents[1] / "backend/documents/sheetjs/node_modules/xlsx"


class SpreadsheetPreviewTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def rewrite_sheet(self, book, replacements):
        source = self.root / "original.xlsx"
        output = self.root / "rewritten.xlsx"
        book.save(source)
        with ZipFile(source) as original, ZipFile(output, "w", ZIP_DEFLATED) as updated:
            for name in original.namelist():
                data = original.read(name)
                if name == "xl/worksheets/sheet1.xml":
                    for old, new in replacements:
                        self.assertIn(old, data)
                        data = data.replace(old, new)
                updated.writestr(name, data)
        return output

    def test_xlsx_uses_cached_formula_values_and_bounds_long_preview_cells(self):
        book = Workbook()
        sheet = book.active
        sheet.append(["=1+1", "=1=3", "=42", "=1/0", date(2026, 11, 23), "x" * 1000])
        path = self.rewrite_sheet(book, [
            (b'<c r="A1"><f>1+1</f><v></v></c>', b'<c r="A1"><f>1+1</f><v>0</v></c>'),
            (b'<c r="B1"><f>1=3</f><v></v></c>', b'<c r="B1" t="b"><f>1=3</f><v>0</v></c>'),
            (b'<c r="D1"><f>1/0</f><v></v></c>', b'<c r="D1" t="e"><f>1/0</f><v>#DIV/0!</v></c>'),
        ])
        first = preview_spreadsheet(path, None, 1, 1)["rows"][0]
        self.assertEqual(first[:5], ["0", "FALSE", None, "#DIV/0!", "2026-11-23T00:00:00"])
        self.assertEqual(first[5], "x" * 255 + "…")
        [page] = extract_spreadsheet(path)
        self.assertEqual(page.spreadsheet["range"], "A1:F1")
        self.assertIn("x" * 1000, page.raw_text)
        self.assertNotIn("=42", page.raw_text)

    def test_sparse_workbook_ignores_wrong_dimension_and_reaches_excel_grid_edge(self):
        book = Workbook()
        book.active["A1"] = "start"
        book.active["XFD1048576"] = "end"
        path = self.rewrite_sheet(book, [(b'A1:XFD1048576', b'A1')])
        first = preview_spreadsheet(path, None, 1, 1)
        self.assertEqual((first["total_rows"], first["total_columns"]), (1_048_576, 16_384))
        self.assertEqual((len(first["rows"]), len(first["columns"])), (40, 20))
        last = preview_spreadsheet(path, "Sheet", 1_048_576, 16_384)
        self.assertEqual(last["columns"], ["XFD"])
        self.assertEqual(last["rows"], [["end"]])
        self.assertEqual([page.spreadsheet["range"] for page in extract_spreadsheet(path)], ["A1", "XFD1048576"])

    def test_compressed_metadata_limit_is_applied_before_loading_workbook(self):
        path = self.root / "oversize.xlsx"
        with ZipFile(path, "w", ZIP_DEFLATED) as archive:
            archive.writestr("xl/sharedStrings.xml", b"a" * (8 * 1024 * 1024 + 1))
        self.assertLess(path.stat().st_size, 10_000)
        with self.assertRaisesRegex(ProcessingError, "解压后过大"):
            preview_spreadsheet(path, None, 1, 1)

    @unittest.skipUnless(shutil.which("node") and SHEETJS.is_dir(), "requires Node.js and SheetJS")
    def test_xls_reads_biff_cached_values_errors_and_dates(self):
        path = self.root / "legacy.xls"
        subprocess.run(["node", "-e", """
            const XLSX = require(process.argv[1]);
            const book = XLSX.utils.book_new();
            const sheet = XLSX.utils.aoa_to_sheet([[0, false]]);
            sheet.A1.bf = [3, 0, 0x1e, 99, 0];
            sheet.C1 = {t: 'z', bf: [3, 0, 0x1e, 42, 0]};
            sheet.D1 = {t: 'n', v: (Date.UTC(2026, 10, 23) - Date.UTC(1899, 11, 30)) / 86400000, z: 'yyyy-mm-dd'};
            sheet.E1 = {t: 'e', v: 7};
            sheet['!ref'] = 'A1:E1';
            XLSX.utils.book_append_sheet(book, sheet, 'Legacy');
            XLSX.writeFile(book, process.argv[2], {bookType: 'biff8'});
        """, str(SHEETJS), str(path)], check=True)
        self.assertEqual(path.read_bytes()[:8], bytes.fromhex("d0cf11e0a1b11ae1"))
        self.assertEqual(preview_spreadsheet(path, None, 1, 1)["rows"],
                         [["0", "FALSE", None, "2026-11-23", "#DIV/0!"]])
        [page] = extract_spreadsheet(path)
        self.assertEqual(page.spreadsheet["range"], "A1:E1")
