import asyncio
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException, UploadFile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.document_loader import DocumentLoader
from backend.ingestion import ExtractedPage
from backend.milvus_writer import MilvusWriter
from backend.models import KnowledgeBase, KnowledgeDocument, KnowledgeDocumentPage, User
from backend.routers import knowledges as routes
from backend.services import knowledge_service as service
from backend.services.knowledge_tools import retrieve


class MemoryMilvus:
    def __init__(self):
        self.rows = []

    def init_collection(self):
        pass

    def query_all(self, filter_expr="", output_fields=None):
        return [r for r in self.rows if not filter_expr or r["document_id"] in filter_expr]

    def delete(self, expression):
        self.rows = [r for r in self.rows if r["document_id"] not in expression]

    def insert(self, payload):
        self.rows.extend(payload)

    def flush(self):
        pass


class SmallEmbeddings:
    def __init__(self):
        self.count = 0

    def increment_add_documents(self, texts):
        self.count += len(texts)

    def increment_remove_documents(self, texts):
        self.count -= len(texts)

    def get_all_embeddings(self, texts):
        return [[1., 0.] for _ in texts], [{0: 1.} for _ in texts]


class DocumentIngestionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.engine = create_engine(f"sqlite:///{self.root / 'db.sqlite'}")
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db = self.factory()
        self.db.add_all([User(id=1, username="alice", password_hash="x"),
                         User(id=2, username="bob", password_hash="x"),
                         KnowledgeBase(id="a", owner_id=1, name="A"),
                         KnowledgeBase(id="b", owner_id=2, name="B")])
        self.db.commit()
        self.milvus, self.embeddings = MemoryMilvus(), SmallEmbeddings()
        self.patches = [patch.object(service, "STORAGE_DIR", self.root / "files"),
                        patch.object(service, "SessionLocal", self.factory),
                        patch.object(service, "document_loader", DocumentLoader()),
                        patch.object(service, "milvus_manager", self.milvus),
                        patch.object(service, "embedding_service", self.embeddings),
                        patch.object(service, "milvus_writer", MilvusWriter(self.embeddings, self.milvus))]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.db.close()
        self.engine.dispose()
        self.temp.cleanup()

    def upload(self, filename="a.txt", data=b"Year 2025 amount 123.45% v2.0\r\n" * 20, owner=1, kb="a"):
        return asyncio.run(service.save_upload(self.db, owner, kb, UploadFile(file=io.BytesIO(data), filename=filename)))

    def test_txt_lifecycle_cleaned_vectors_duplicate_scope_and_reprocessing(self):
        doc = self.upload()
        service.process_document(doc.id)
        self.db.expire_all()
        self.assertEqual(doc.status, "ready")
        self.assertEqual(doc.processing_report["indexed_pages"], 1)
        self.assertIn(".txt", routes.knowledge_settings()["allowed_extensions"])
        self.assertTrue(all("\r" not in r["text"] and len(r["dense_embedding"]) == 2 for r in self.milvus.rows))
        with self.assertRaises(HTTPException) as duplicate:
            self.upload("different-name.txt")
        self.assertEqual(duplicate.exception.status_code, 409)
        self.upload(owner=2, kb="b")  # Same content in another user's scope is allowed.
        original_count = self.embeddings.count
        service.queue_reprocessing(self.db, 1, "a", doc.id)
        self.assertEqual(service.visible_document_ids(self.db, 1), [])
        service.process_document(doc.id)
        self.db.expire_all()
        self.assertEqual(doc.status, "ready")
        self.assertEqual(self.embeddings.count, original_count)
        self.assertEqual(self.db.query(KnowledgeDocumentPage).filter_by(document_id=doc.id).count(), 1)

    def test_partial_pdf_diagnostics_retrieval_warnings_and_private_page_access(self):
        doc = self.upload("scan.pdf", b"%PDF-test")
        pages = [ExtractedPage(0, "Usable 2025 amount 123.45"),
                 ExtractedPage(1, "", status="failed", warnings=["Page failed"])]
        with patch("backend.document_loader.extract_pdf", return_value=pages):
            service.process_document(doc.id)
        self.db.expire_all()
        self.assertEqual(doc.status, "ready")
        self.assertEqual(doc.processing_report["incomplete_pages"], [2])
        alice = self.db.get(User, 1)
        detail = routes.page_details("a", doc.id, 2, alice, self.db)
        self.assertEqual(detail["page_number"], 2)
        self.assertEqual(detail["status"], "failed")
        with self.assertRaises(HTTPException) as denied:
            routes.page_details("a", doc.id, 2, self.db.get(User, 2), self.db)
        self.assertEqual(denied.exception.status_code, 404)
        result = {"docs": [self.milvus.rows[0]], "meta": {"retrieval_mode": "hybrid"}}
        with patch("backend.rag.retrieval.retrieve_documents", return_value=result):
            hit = retrieve(self.db, 1, "2025", ["a"])["results"][0]
        self.assertEqual(hit["processingReport"]["incomplete_pages"], [2])
        from backend.mcp_knowledge import RetrievalResult
        self.assertEqual(RetrievalResult.model_validate({"status": "ok", "results": [hit]}).results[0].processingReport["incomplete_pages"], [2])
        service.delete_document(self.db, doc)
        self.assertEqual(self.db.query(KnowledgeDocumentPage).count(), 0)
        self.assertEqual(self.embeddings.count, 0)

    def test_all_failed_pages_keep_report_and_never_become_searchable(self):
        doc = self.upload("bad.pdf", b"%PDF-test")
        with patch("backend.document_loader.extract_pdf", return_value=[
            ExtractedPage(0, "", status="failed", warnings=["Page failed"]) ]):
            service.process_document(doc.id)
        self.db.expire_all()
        self.assertEqual(doc.status, "failed")
        self.assertEqual(doc.processing_report["incomplete_pages"], [1])
        self.assertEqual(service.visible_document_ids(self.db, 1), [])
        self.assertEqual(self.milvus.rows, [])

    def test_txt_binary_blank_and_foreign_reprocess_are_rejected(self):
        for data in (b"binary\x00file", b"\r\n\t "):
            with self.assertRaises(HTTPException) as invalid:
                self.upload(data=data)
            self.assertEqual(invalid.exception.status_code, 400)
        doc = self.upload()
        with self.assertRaises(HTTPException):
            service.queue_reprocessing(self.db, 2, "a", doc.id)


if __name__ == "__main__":
    unittest.main()
