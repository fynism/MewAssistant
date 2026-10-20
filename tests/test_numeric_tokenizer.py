import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.infrastructure.embedding import EmbeddingService


class NumericTokenizerTests(unittest.TestCase):
    def test_new_state_preserves_numbers_and_identifiers_across_restart(self):
        with tempfile.TemporaryDirectory() as tmp, patch("backend.infrastructure.embedding._create_dense_embedder"):
            path = Path(tmp) / "state.json"
            service = EmbeddingService(path)
            tokens = service.tokenize("2025 123.45 v2.0 AB-123 GB/T 3.5%")
            self.assertEqual(tokens, ["2025", "123.45", "v2.0", "ab-123", "gb/t", "3.5"])
            service.increment_add_documents(["2025 amount 123.45 v2.0"])
            restarted = EmbeddingService(path)
            self.assertEqual(restarted._tokenizer_version, 2)
            self.assertTrue(restarted.get_sparse_embedding("2025"))

    def test_legacy_state_does_not_silently_switch_dimensions(self):
        with tempfile.TemporaryDirectory() as tmp, patch("backend.infrastructure.embedding._create_dense_embedder"):
            path = Path(tmp) / "state.json"
            path.write_text(json.dumps({"version": 1, "total_docs": 1, "sum_token_len": 1,
                                        "vocab": {"v": 0}, "doc_freq": {"v": 1}}))
            service = EmbeddingService(path)
            self.assertEqual(service.tokenize("2025 v2"), ["v"])
            service.increment_remove_documents(["2025 v2"])
            service.reset_for_rebuild()
            self.assertEqual(service.tokenize("2025 v2"), ["2025", "v2"])
            self.assertEqual(service._total_docs, 0)
            self.assertEqual(service._vocab, {})


if __name__ == "__main__":
    unittest.main()
