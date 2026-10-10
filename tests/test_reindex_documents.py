import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.models import KnowledgeBase, KnowledgeDocument, User
from backend.reindex_documents import reindex_all


class ReindexTests(unittest.TestCase):
    def test_old_vectors_are_removed_before_new_tokenizer_and_documents_are_hidden(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = create_engine(f"sqlite:///{Path(tmp) / 'test.db'}")
            Base.metadata.create_all(engine)
            factory = sessionmaker(bind=engine, expire_on_commit=False)
            with factory() as db:
                db.add_all([User(id=1, username="a", password_hash="x"),
                            KnowledgeBase(id="kb", owner_id=1, name="KB"),
                            KnowledgeDocument(id="doc", knowledge_id="kb", filename="old.txt",
                                              storage_key="old.txt", file_type=".txt", status="ready")])
                db.commit()
            calls = []
            def cleanup(db, doc, remove_bm25):
                self.assertEqual(doc.status, "pending")
                self.assertTrue(remove_bm25)
                calls.append("cleanup")
            def process(doc_id):
                calls.append("process")
                with factory() as db:
                    db.get(KnowledgeDocument, doc_id).status = "ready"
                    db.commit()
            with (patch("backend.database.SessionLocal", factory),
                  patch("backend.services.knowledge_service._cleanup_vectors", side_effect=cleanup),
                  patch("backend.services.knowledge_service.process_document", side_effect=process),
                  patch("backend.dependencies.milvus_manager") as milvus,
                  patch("backend.dependencies.embedding_service") as embedding):
                milvus.query_all.return_value = []
                embedding.reset_for_rebuild.side_effect = lambda: calls.append("reset")
                self.assertEqual(reindex_all(True), {"total": 1, "ready": 1, "failed": 0})
                self.assertEqual(calls, ["cleanup", "reset", "process"])
                milvus.query_all.return_value = [{"document_id": "orphan"}]
                with self.assertRaises(RuntimeError):
                    reindex_all(True)
                self.assertEqual(embedding.reset_for_rebuild.call_count, 1)
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
