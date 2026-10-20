import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import numpy as np
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from backend.documents import pdf
from backend.documents.cleaning import TextBlock, clean_pages, summarize_pages


def write_native_pdf(path: Path):
    writer = PdfWriter()
    page = writer.add_blank_page(612, 792)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                             NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    content = DecodedStreamObject()
    content.set_data(b"BT /F1 14 Tf 40 700 Td (SuperMew ordinary native document: year 2025 amount 123.45 and version v2.0.) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(content)
    writer.write(path)


class PDFExtractionTests(unittest.TestCase):
    def settings(self, **kwargs):
        return patch.object(pdf, "settings", replace(pdf.settings, **kwargs))

    def test_native_pdf_skips_ocr_and_retains_geometry(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "native.pdf"
            write_native_pdf(path)
            with patch.object(pdf, "_recognize") as ocr:
                pages = pdf.extract_pdf(str(path))
        ocr.assert_not_called()
        self.assertIn("2025", pages[0].raw_text)
        self.assertTrue(pages[0].blocks[0].bbox)
        self.assertEqual(pages[0].status, "ready")

    def test_image_page_with_long_native_header_still_gets_ocr_without_duplicates(self):
        header = "A native header with sufficient letters to exceed the minimum threshold"
        native = TextBlock(header, (.1, .02, .9, .06))
        recognized = [TextBlock(header, (.1, .02, .9, .06), .99, "ocr"),
                      TextBlock("Scanned body 2025 amount 123.45", (.1, .3, .9, .35), .98, "ocr")]
        with (patch.object(pdf, "_pdf_info", return_value=1),
              patch.object(pdf, "_inspect_page", return_value=(header, [native], .9)),
              patch.object(pdf, "_render_page", return_value=np.zeros((10, 10, 3))),
              patch.object(pdf, "_recognize", return_value=recognized) as ocr):
            pages = pdf.extract_pdf("test")
        ocr.assert_called_once()
        self.assertEqual(pages[0].raw_text.count(header), 1)
        self.assertIn("123.45", pages[0].raw_text)
        self.assertEqual(pages[0].extraction, "mixed")
        self.assertEqual(len(pages[0].blocks), 3)

    def test_failure_on_one_page_does_not_drop_other_pages(self):
        with (patch.object(pdf, "_pdf_info", return_value=2),
              patch.object(pdf, "_inspect_page", side_effect=[("Normal native content " * 5, [], 0), ("", [], 1)]),
              patch.object(pdf, "_render_page", side_effect=RuntimeError("internal private path"))):
            pages = pdf.extract_pdf("test")
        clean_pages(pages)
        report = summarize_pages(pages)
        self.assertEqual(report["indexed_pages"], 1)
        self.assertEqual(report["incomplete_pages"], [2])
        self.assertNotIn("private", str(report))

    def test_blank_page_is_distinguished_from_nonblank_unrecognized_page(self):
        for color, expected in [(255, "blank"), (0, "skipped")]:
            with (self.settings(pdf_ocr_retry_dpi=pdf.settings.pdf_ocr_dpi),
                  patch.object(pdf, "_pdf_info", return_value=1),
                  patch.object(pdf, "_inspect_page", return_value=("", [], 0)),
                  patch.object(pdf, "_render_page", return_value=np.full((20, 20, 3), color)),
                  patch.object(pdf, "_recognize", return_value=[])):
                page = pdf.extract_pdf("test")[0]
            self.assertEqual(page.status, expected)

    def test_low_confidence_gets_one_retry_then_is_retained_for_review(self):
        block = TextBlock("incorrect 123", (.1, .1, .8, .2), .1, "ocr")
        with (patch.object(pdf, "_pdf_info", return_value=1),
              patch.object(pdf, "_inspect_page", return_value=("", [], 1)),
              patch.object(pdf, "_render_page", return_value=np.zeros((10, 10, 3))),
              patch.object(pdf, "_recognize", return_value=[block]) as ocr):
            page = pdf.extract_pdf("test")[0]
        self.assertEqual(ocr.call_count, 2)
        self.assertEqual(page.status, "skipped")
        self.assertEqual(page.ocr_text, "incorrect 123")
        self.assertEqual(page.blocks[0].score, .1)

    def test_ocr_off_reports_unindexed_scan(self):
        with (self.settings(pdf_ocr_mode="off"), patch.object(pdf, "_pdf_info", return_value=1),
              patch.object(pdf, "_inspect_page", return_value=("", [], 1)),
              patch.object(pdf, "_recognize") as ocr):
            page = pdf.extract_pdf("test")[0]
        ocr.assert_not_called()
        self.assertEqual(page.status, "skipped")

    def test_pdf_limit_and_password_errors_are_actionable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pages.pdf"
            writer = PdfWriter()
            writer.add_blank_page(100, 100)
            writer.add_blank_page(100, 100)
            writer.write(path)
            with self.settings(max_pdf_pages=1), self.assertRaisesRegex(ValueError, "1"):
                pdf.extract_pdf(str(path))
            writer.encrypt("password")
            writer.write(path)
            with self.assertRaises(ValueError):
                pdf.extract_pdf(str(path))

    def test_rotated_pdf_native_boxes_match_rendered_coordinates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rotated.pdf"
            write_native_pdf(path)
            from pypdf import PdfReader
            reader = PdfReader(path)
            writer = PdfWriter()
            writer.add_page(reader.pages[0].rotate(90))
            writer.write(path)
            _, blocks, _ = pdf._inspect_page(str(path), 0)
            pixels = pdf._render_page(str(path), 0, 72)
        self.assertEqual(pixels.shape[:2], (612, 792))
        self.assertTrue(blocks)
        self.assertTrue(all(0 <= v <= 1 for v in blocks[0].bbox))
        self.assertGreater(blocks[0].bbox[0], .8)

    def test_failed_native_extraction_falls_back_to_ocr(self):
        with (patch.object(pdf, "_pdf_info", return_value=1),
              patch.object(pdf, "_inspect_page", side_effect=RuntimeError("text parse failed")),
              patch.object(pdf, "_render_page", return_value=np.zeros((10, 10, 3))),
              patch.object(pdf, "_recognize", return_value=[TextBlock("Recovered 2025", (.1, .1, .9, .2), .98, "ocr")])):
            page = pdf.extract_pdf("test")[0]
        self.assertEqual(page.status, "ready")
        self.assertEqual(page.extraction, "ocr")
        self.assertIn("2025", page.raw_text)


if __name__ == "__main__":
    unittest.main()
