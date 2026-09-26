import asyncio
import io
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException, UploadFile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.models import KnowledgeBase, KnowledgeDocument, User
from backend.routers import knowledges as routes
from backend.services import knowledge_service as service


class M2DocumentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.engine = create_engine(f"sqlite:///{self.root / 'db.sqlite'}")
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db = self.factory()
        self.db.add_all([
            User(id=1, username="alice", password_hash="x", role="user"),
            User(id=2, username="bob", password_hash="x", role="user"),
            KnowledgeBase(id="alice-kb", owner_id=1, name="A"),
            KnowledgeBase(id="bob-kb", owner_id=2, name="B"),
        ])
        self.db.add(KnowledgeDocument(id="alice-doc", knowledge_id="alice-kb",
            filename="same.pdf", storage_key="old.pdf", file_type=".pdf", status="ready"))
        self.db.commit()
        self.storage = self.root / "files"
        self.storage.mkdir()
        (self.storage / "old.pdf").write_bytes(b"%PDF-old")
        self.storage_patch = patch.object(service, "STORAGE_DIR", self.storage)
        self.storage_patch.start()

    def tearDown(self):
        self.storage_patch.stop()
        self.db.close()
        self.engine.dispose()
        self.temp.cleanup()

    def upload(self):
        return UploadFile(file=io.BytesIO(b"%PDF-new"), filename="same.pdf")

    def test_replacement_immediately_hides_old_document_and_reuses_id(self):
        item = asyncio.run(service.replace_document(
            self.db, 1, "alice-kb", "alice-doc", self.upload()))
        self.assertEqual(item.status, "replacing")
        self.assertEqual(service.visible_document_ids(self.db, 1), [])
        self.assertEqual(item.id, "alice-doc")
        self.assertTrue((self.storage / item.replacement_storage_key).is_file())

        class Milvus:
            deleted = False

            def init_collection(self):
                pass

            def query_all(self, **_kwargs):
                return [] if self.deleted else [{"text": "old chunk"}]

            def delete(self, _expression):
                self.deleted = True

        class Embeddings:
            removed = []

            def increment_remove_documents(self, texts):
                self.removed.extend(texts)

        class Loader:
            def load_document(self, _path, filename, document_id, knowledge_id):
                return [{"chunk_level": 3, "chunk_id": "leaf", "text": "new chunk",
                         "filename": filename, "document_id": document_id,
                         "knowledge_id": knowledge_id, "file_type": "PDF", "page_number": 0}]

        class Writer:
            written = []

            def write_documents(self, chunks):
                self.written.extend(chunks)

        milvus, embeddings, writer = Milvus(), Embeddings(), Writer()
        with (patch.object(service, "SessionLocal", self.factory),
              patch.object(service, "milvus_manager", milvus),
              patch.object(service, "embedding_service", embeddings),
              patch.object(service, "document_loader", Loader()),
              patch.object(service, "milvus_writer", writer)):
            service.process_document(item.id)
        self.db.expire_all()
        self.assertEqual(item.status, "ready")
        self.assertEqual(item.id, "alice-doc")
        self.assertIsNone(item.replacement_storage_key)
        self.assertEqual(service.visible_document_ids(self.db, 1), ["alice-doc"])
        self.assertEqual(embeddings.removed, ["old chunk"])
        self.assertEqual(writer.written[0]["text"], "new chunk")
        self.assertFalse((self.storage / "old.pdf").exists())

    def test_replacement_denies_foreign_and_processing_documents(self):
        with self.assertRaises(HTTPException) as forbidden:
            asyncio.run(service.replace_document(self.db, 2, "alice-kb", "alice-doc", self.upload()))
        self.assertEqual(forbidden.exception.status_code, 404)
        item = self.db.query(KnowledgeDocument).one()
        item.status = "processing"
        self.db.commit()
        with self.assertRaises(HTTPException) as conflict:
            asyncio.run(service.replace_document(self.db, 1, "alice-kb", "alice-doc", self.upload()))
        self.assertEqual(conflict.exception.status_code, 409)

    def test_management_payload_reports_ready_count_and_upload_limits(self):
        alice = self.db.query(User).filter(User.id == 1).one()
        payload = routes.list_knowledges(user=alice, db=self.db)["knowledges"]
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["ready_document_count"], 1)
        self.assertTrue(payload[0]["has_ready_documents"])
        settings = routes.knowledge_settings()
        self.assertIn(".pdf", settings["allowed_extensions"])
        self.assertGreater(settings["max_upload_bytes"], 0)
        document = self.db.query(KnowledgeDocument).one()
        self.assertIn("updated_at", routes.document_payload(document))

    def test_user_document_and_storage_quotas_cover_upload_and_replacement(self):
        with patch.object(service, "settings", replace(service.settings, max_user_documents=1)):
            with self.assertRaises(HTTPException) as full:
                asyncio.run(service.save_upload(self.db, 1, "alice-kb", self.upload()))
        self.assertEqual(full.exception.status_code, 413)
        self.assertEqual(self.db.query(KnowledgeDocument).count(), 1)

        with patch.object(service, "settings", replace(service.settings, max_user_storage_bytes=14)):
            with self.assertRaises(HTTPException) as full:
                asyncio.run(service.replace_document(self.db, 1, "alice-kb", "alice-doc", self.upload()))
        self.assertEqual(full.exception.status_code, 413)
        self.assertEqual(self.db.query(KnowledgeDocument).one().status, "ready")


if __name__ == "__main__":
    unittest.main()
