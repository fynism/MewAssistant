import unittest


class M1WriterTests(unittest.TestCase):
    def test_second_batch_failure_deletes_partial_vectors_and_bm25_stats(self):
        from backend.milvus_writer import MilvusWriter

        class Embeddings:
            def __init__(self):
                self.added = []
                self.removed = []

            def increment_add_documents(self, texts):
                self.added.extend(texts)

            def increment_remove_documents(self, texts):
                self.removed.extend(texts)

            def get_all_embeddings(self, texts):
                return [[1.0] for _ in texts], [{1: 1.0} for _ in texts]

        class Milvus:
            def __init__(self):
                self.insert_calls = 0
                self.delete_expr = None

            def init_collection(self):
                pass

            def insert(self, payload):
                self.insert_calls += 1
                if self.insert_calls == 2:
                    raise RuntimeError("second batch failed")

            def delete(self, expression):
                self.delete_expr = expression

        embeddings, milvus = Embeddings(), Milvus()
        writer = MilvusWriter(embedding_service=embeddings, milvus_manager=milvus)
        docs = [{"document_id": "private-doc", "knowledge_id": "private-kb",
                 "text": text, "filename": "same.pdf", "file_type": "PDF",
                 "chunk_id": f"private-doc-{index}", "chunk_level": 3}
                for index, text in enumerate(["first", "second"])]
        with self.assertRaisesRegex(RuntimeError, "second batch failed"):
            writer.write_documents(docs, batch_size=1)
        self.assertEqual(milvus.delete_expr, 'document_id == "private-doc"')
        self.assertEqual(embeddings.added, ["first", "second"])
        self.assertEqual(embeddings.removed, ["first", "second"])


if __name__ == "__main__":
    unittest.main()
