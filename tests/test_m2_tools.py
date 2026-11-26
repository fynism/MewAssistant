import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image, ImageChops
from pypdf import PdfReader, PdfWriter
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.core.security import create_access_token
from backend.documents.loader import DocumentLoader
from backend.api.dependencies import get_db
from backend.infrastructure import storage
from backend.infrastructure.database.session import Base
from backend.infrastructure.database.models import KnowledgeBase, KnowledgeDocument, User
from tests.test_pdf_extraction import write_native_pdf


class M2ToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from backend.app import app
        cls.app = app

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self.temp.name) / 'db.sqlite'}")
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.rate_patch = patch("backend.api.routers.tool_debug.enforce_rate_limits", lambda *_: None)
        self.rate_patch.start()

        def db_override():
            with self.factory() as db:
                yield db

        self.app.dependency_overrides[get_db] = db_override
        self.client = TestClient(self.app)
        with self.factory() as db:
            db.add_all([
                User(id=1, username="alice", password_hash="x", role="user"),
                User(id=2, username="bob", password_hash="x", role="user"),
                KnowledgeBase(id="alice-kb", owner_id=1, name="Alice's KB"),
                KnowledgeBase(id="bob-kb", owner_id=2, name="Bob's KB"),
                KnowledgeDocument(id="alice-doc", knowledge_id="alice-kb", filename="same.pdf",
                    storage_key="a.pdf", file_type=".pdf", status="ready"),
                KnowledgeDocument(id="bob-doc", knowledge_id="bob-kb", filename="same.pdf",
                    storage_key="b.pdf", file_type=".pdf", status="ready"),
            ])
            db.commit()
        self.alice = {"Authorization": "Bearer " + create_access_token("alice", "user")}
        self.bob = {"Authorization": "Bearer " + create_access_token("bob", "user")}

    def tearDown(self):
        self.rate_patch.stop()
        self.client.close()
        self.app.dependency_overrides.clear()
        self.engine.dispose()
        self.temp.cleanup()

    def test_list_is_private_and_paginated(self):
        self.assertEqual(self.client.post("/tools/debug/listKnowledges", json={}).status_code, 401)
        response = self.client.post("/tools/debug/listKnowledges", headers=self.alice, json={"limit": 1})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([item["id"] for item in response.json()["items"]], ["alice-kb"])
        self.assertEqual(response.json()["items"][0]["readyDocumentCount"], 1)
        self.assertIsNone(response.json()["nextCursor"])
        self.assertEqual(self.client.post("/tools/debug/listKnowledges", headers=self.alice,
            json={"cursor": "%%%"}).status_code, 422)

    def test_retrieve_requires_scope_and_rejects_foreign_knowledge(self):
        endpoint = "/tools/debug/retrieve"
        self.assertEqual(self.client.post(endpoint, headers=self.alice,
            json={"query": "secret"}).status_code, 422)
        self.assertEqual(self.client.post(endpoint, headers=self.alice,
            json={"query": "secret", "knowledgeIds": []}).status_code, 422)
        self.assertEqual(self.client.post(endpoint, headers=self.alice,
            json={"query": "secret", "knowledgeIds": ["alice-kb", "alice-kb"]}).status_code, 422)
        self.assertEqual(self.client.post(endpoint, headers=self.alice,
            json={"query": "secret", "knowledgeIds": ["bob-kb"]}).status_code, 404)
        self.assertEqual(self.client.post(endpoint, headers=self.alice,
            json={"query": "   "}).status_code, 422)

        hit = {"document_id": "alice-doc", "knowledge_id": "alice-kb",
               "filename": "untrusted.pdf", "chunk_id": "leaf-1", "page_number": 2,
               "text": "Alice only", "score": 0.7}
        foreign = {"document_id": "bob-doc", "knowledge_id": "bob-kb",
                   "chunk_id": "foreign", "text": "Bob only"}
        with patch("backend.rag.retrieval.retrieve_documents",
                   return_value={"docs": [hit, foreign], "meta": {"retrieval_mode": "hybrid"}}) as search:
            response = self.client.post(endpoint, headers=self.alice,
                json={"query": "secret", "knowledgeIds": ["alice-kb"], "topK": 2})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "ok")
        self.assertEqual(len(response.json()["results"]), 1)
        self.assertEqual(response.json()["results"][0]["filename"], "same.pdf")
        self.assertEqual(response.json()["results"][0]["sourceId"], "alice-doc:leaf-1")
        self.assertEqual(search.call_args.kwargs["knowledge_ids"], ["alice-kb"])

        with patch("backend.rag.retrieval.retrieve_documents",
                   return_value={"docs": [], "meta": {"retrieval_mode": "hybrid"}}):
            self.assertEqual(self.client.post(endpoint, headers=self.alice,
                json={"query": "missing", "knowledgeIds": ["alice-kb"]}).json()["status"], "no_match")
        with patch("backend.rag.retrieval.retrieve_documents",
                   return_value={"docs": [], "meta": {"retrieval_mode": "failed"}}):
            self.assertEqual(self.client.post(endpoint, headers=self.alice,
                json={"query": "error", "knowledgeIds": ["alice-kb"]}).status_code, 503)

    def test_workspace_browses_and_searches_only_owned_documents(self):
        self.assertEqual(self.client.get("/documents").status_code, 401)
        listing = self.client.get("/documents", headers=self.alice).json()
        self.assertEqual([item["id"] for item in listing["documents"]], ["alice-doc"])
        hit = {"document_id": "alice-doc", "page_number": 0, "text": "target"}
        foreign = {"document_id": "bob-doc", "page_number": 0, "text": "target"}
        with (patch("backend.api.routers.knowledges.enforce_rate_limits"),
              patch("backend.rag.retrieval.retrieve_documents",
                    return_value={"docs": [hit, foreign], "meta": {}}) as search):
            endpoint = "/documents/search"
            response = self.client.post(endpoint, headers=self.alice, json={"query": "target"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual([item["documentId"] for item in response.json()["results"]], ["alice-doc"])
            self.assertIsNone(search.call_args.kwargs["knowledge_ids"])
            query = {"query": "target", "knowledgeId": "alice-kb"}
            self.assertEqual(self.client.post(endpoint, headers=self.alice, json=query).status_code, 200)
            self.assertEqual(search.call_args.kwargs["knowledge_ids"], ["alice-kb"])
            query["knowledgeId"] = "bob-kb"
            self.assertEqual(self.client.post(endpoint, headers=self.alice, json=query).status_code, 404)

    def test_pdf_preview_highlights_native_text_and_protects_original(self):
        root = Path(self.temp.name)
        original = root / "a.pdf"
        write_native_pdf(original)
        endpoint = "/knowledges/alice-kb/documents/alice-doc"
        with patch.object(storage, "KNOWLEDGE_DOCUMENTS_DIR", root):
            response = self.client.get(endpoint + "/content", headers=self.alice)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, original.read_bytes())
            self.assertIn("attachment", response.headers["content-disposition"])
            self.assertIn("no-store", response.headers["cache-control"])
            self.assertEqual(self.client.get(endpoint + "/content", headers=self.bob).status_code, 404)
            self.assertEqual(self.client.post(endpoint + "/preview/1", json={}).status_code, 401)
            self.assertEqual(self.client.post(endpoint + "/preview/1",
                headers=self.bob, json={}).status_code, 404)
            self.assertEqual(self.client.post(endpoint + "/preview/2",
                headers=self.alice, json={}).status_code, 404)
            preview = self.client.post(endpoint + "/preview/1", headers=self.alice, json={"query": "2025"})
            plain = self.client.post(endpoint + "/preview/1", headers=self.alice, json={})
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.headers["x-pdf-page-count"], "1")
        self.assertEqual(preview.headers["content-type"], "image/png")
        with Image.open(io.BytesIO(preview.content)) as marked, Image.open(io.BytesIO(plain.content)) as unmarked:
            self.assertEqual(max(marked.size), 1600)
            self.assertIsNotNone(ImageChops.difference(marked, unmarked).getbbox())

    def test_old_word_previews_on_demand_and_reprocessing_reuses_the_same_pages(self):
        root = Path(self.temp.name)
        original = root / "a.docx"
        original.write_bytes(b"old Word original")
        with self.factory() as db:
            old = db.get(KnowledgeDocument, "alice-doc")
            old.filename = "sample.docx"
            old.storage_key = original.name
            old.file_type = ".docx"
            db.commit()

        fixture = root / "native.pdf"
        write_native_pdf(fixture)

        def convert(source, work_dir):
            writer = PdfWriter()
            for _ in range(2):
                writer.add_page(PdfReader(fixture).pages[0])
            writer.write(work_dir / f"{source.stem}.pdf")

        endpoint = "/knowledges/alice-kb/documents/alice-doc"
        with (patch.object(storage, "KNOWLEDGE_DOCUMENTS_DIR", root),
              patch("backend.documents.word._run_conversion", side_effect=convert) as converter):
            self.assertEqual(self.client.post(endpoint + "/preview/1", json={}).status_code, 401)
            self.assertEqual(self.client.post(endpoint + "/preview/1", headers=self.bob, json={}).status_code, 404)
            converter.assert_not_called()
            preview = self.client.post(endpoint + "/preview/2", headers=self.alice, json={"query": "2025"})
            self.assertEqual(preview.status_code, 200, preview.text if preview.status_code != 200 else "")
            self.assertEqual(preview.headers["x-pdf-page-count"], "2")
            self.assertEqual(self.client.post(endpoint + "/preview/3", headers=self.alice, json={}).status_code, 404)
            self.assertEqual(self.client.get(endpoint + "/content", headers=self.alice).content, original.read_bytes())
            chunks = DocumentLoader().load_document(str(original), old.filename, old.id, old.knowledge_id)
            self.assertEqual({c["page_number"] for c in chunks}, {0, 1})
            self.assertEqual({c["file_type"] for c in chunks}, {"Word"})
            converter.assert_called_once()


if __name__ == "__main__":
    unittest.main()
