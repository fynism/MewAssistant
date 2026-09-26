import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.auth import create_access_token, get_db
from backend.database import Base
from backend.models import KnowledgeBase, KnowledgeDocument, User


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


if __name__ == "__main__":
    unittest.main()
