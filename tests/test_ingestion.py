import tempfile
import unittest
from pathlib import Path

from backend.documents.loader import DocumentLoader
from backend.documents.cleaning import (ExtractedPage, ProcessingError, TextBlock, clean_pages,
                               clean_text, decode_text, reconstruct_text, public_report)


class CleaningTests(unittest.TestCase):
    def test_cleaning_preserves_quantities_columns_and_indentation(self):
        raw = "\ufeff  name\tamount\r\nA    123.45%\u00a0\r\n\r\n\r\n\u200b2025 v2.0 GB/T 12345\x00"
        self.assertEqual(clean_text(raw), "  name\tamount\nA    123.45%\n\n2025 v2.0 GB/T 12345")
        self.assertEqual(clean_text(clean_text(raw)), clean_text(raw))

    def test_txt_supported_encodings_and_invalid_binary(self):
        text = "\u4e2d\u6587 2025 v2.0"
        for encoding in ("utf-8", "utf-8-sig", "utf-16", "utf-32", "gb18030"):
            with self.subTest(encoding=encoding):
                self.assertEqual(decode_text(text.encode(encoding)), text)
        for data in (b"abc\x00def", b"\x89PNG\r\n\x1a\n\x00", b"\xff\xfe\x01"):
            with self.assertRaises(ProcessingError):
                decode_text(data)

    def test_only_repeated_geometric_margins_are_removed(self):
        pages = []
        for n in range(3):
            blocks = [TextBlock("Company manual", (.1, .02, .9, .05)),
                      TextBlock("Repeated body must stay", (.1, .3, .9, .4)),
                      TextBlock(f"Section {n + 1}: amount 123.45", (.1, .5, .9, .6)),
                      TextBlock(str(n + 1), (.4, .96, .6, .99))]
            pages.append(ExtractedPage(n, reconstruct_text(blocks), blocks=blocks))
        clean_pages(pages, remove_margins=True)
        for p in pages:
            self.assertNotIn("Company manual", p.cleaned_text)
            self.assertIn("Company manual", p.raw_text)
            self.assertIn("Repeated body must stay", p.cleaned_text)
            self.assertIn("123.45", p.cleaned_text)

    def test_two_column_reading_order_and_spanning_title(self):
        blocks = [TextBlock("Right 2", (.6, .3, .9, .34)),
                  TextBlock("Title", (.1, .01, .9, .08))]
        for n in range(3):
            blocks.append(TextBlock(f"Left {n}", (.1, .1 + n * .1, .4, .15 + n * .1)))
            blocks.append(TextBlock(f"Right {n}", (.6, .1 + n * .1, .9, .15 + n * .1)))
        blocks = [b for b in blocks if b.text != "Right 2"] + [TextBlock("Right 2", (.6, .3, .9, .35))]
        text = reconstruct_text(blocks)
        self.assertLess(text.index("Title"), text.index("Left 0"))
        self.assertLess(text.index("Left 2"), text.index("Right 0"))

    def test_chinese_prose_join_does_not_join_list_heading(self):
        prose = "\u8fd9\u662f\u4e00\u6bb5\u9700\u8981\u4fdd\u7559\u7684\u4e2d\u6587\u6b63\u6587" * 2
        blocks = [TextBlock(prose, (.1, .1, .9, .12)),
                  TextBlock(prose, (.1, .125, .9, .145)),
                  TextBlock("\u7b2c\u4e8c\u7ae0" + prose, (.1, .15, .9, .17))]
        result = reconstruct_text(blocks, True)
        self.assertTrue(result.startswith(prose * 2 + "\n\u7b2c\u4e8c\u7ae0"))

    def test_all_levels_use_cleaned_text_and_keep_parent_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.txt"
            path.write_text("\ufeff" + ("2025 v2.0 amount 123.45%\u200b\r\n" * 80), encoding="utf-8")
            chunks = DocumentLoader().load_document(str(path), "sample.TXT", "doc", "kb")
        self.assertEqual(chunks.report["indexed_pages"], 1)
        parents = {c["chunk_id"] for c in chunks if c["chunk_level"] in (1, 2)}
        for c in chunks:
            self.assertNotIn("\ufeff", c["text"])
            self.assertNotIn("\u200b", c["text"])
            self.assertEqual(c["document_id"], "doc")
            self.assertEqual(c["file_type"], "TXT")
            if c["chunk_level"] == 3:
                self.assertIn(c["parent_chunk_id"], parents)

    def test_retrieval_report_is_bounded_without_hiding_missing_pages(self):
        report = {"total_pages": 300, "incomplete_pages": list(range(1, 301)),
                  "warnings": [{"page": n, "messages": ["OCR failed"]} for n in range(300)]}
        brief = public_report(report)
        self.assertEqual(len(brief["warnings"]), 10)
        self.assertEqual(len(brief["incomplete_pages"]), 300)
        self.assertEqual(brief["warning_pages"], 300)


if __name__ == "__main__":
    unittest.main()
