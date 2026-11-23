import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from backend.documents.cleaning import ProcessingError
from backend.documents.word import WordConversionBusy, _conversion_lock, word_pdf
from backend.infrastructure import storage
from tests.test_pdf_extraction import write_native_pdf


class WordPreviewTests(unittest.TestCase):
    def setUp(self):
        temp = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(temp).resolve()
        self.source = self.root / "word.docx"
        self.source.write_bytes(b"original")
        self.enterContext(patch.object(storage, "KNOWLEDGE_DOCUMENTS_DIR", self.root))

    @staticmethod
    def convert(source, work_dir):
        write_native_pdf(work_dir / f"{source.stem}.pdf")

    def test_concurrent_cache_misses_convert_once_and_cleanup_removes_artifact(self):
        with (patch("backend.documents.word._run_conversion", side_effect=self.convert) as convert,
              ThreadPoolExecutor(max_workers=3) as pool):
            cached = list(pool.map(word_pdf, [self.source] * 3))
        self.assertEqual(cached, [cached[0]] * 3)
        convert.assert_called_once()
        self.assertEqual(list(cached[0].parent.iterdir()), [cached[0]])
        self.assertEqual(self.source.read_bytes(), b"original")
        storage.remove_knowledge_file(self.source.name)
        self.assertFalse(self.source.exists())
        self.assertFalse(cached[0].exists())

    def test_busy_preview_releases_web_threads_but_cached_preview_still_works(self):
        with _conversion_lock, self.assertRaises(WordConversionBusy):
            word_pdf(self.source, wait=False)
        with patch("backend.documents.word._run_conversion", side_effect=self.convert):
            cached = word_pdf(self.source)
        with _conversion_lock:
            self.assertEqual(word_pdf(self.source, wait=False), cached)

    def test_missing_output_is_not_cached(self):
        with (patch("backend.documents.word._run_conversion"),
              self.assertRaisesRegex(ProcessingError, "无法转换 Word 文件")):
            word_pdf(self.source)
        self.assertEqual(list((self.root / ".previews").iterdir()), [])

    def test_conversion_cannot_restore_cache_for_a_deleted_original(self):
        def convert_during_delete(source, work_dir):
            self.convert(source, work_dir)
            storage.remove_knowledge_file(source.name)

        with (patch("backend.documents.word._run_conversion", side_effect=convert_during_delete),
              self.assertRaises(FileNotFoundError)):
            word_pdf(self.source)
        self.assertEqual(list((self.root / ".previews").iterdir()), [])
