"""M1 authorization and retrieval checks using a disposable database."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.auth import get_db, get_password_hash
from backend.database import Base
from backend.models import KnowledgeBase, KnowledgeDocument, User


class M1IsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from backend.app import app
        cls.app = app

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self.temp.name) / 'test.db'}")
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)

        def test_db():
            with self.session_factory() as db:
                yield db

        self.app.dependency_overrides[get_db] = test_db
        self.client = TestClient(self.app)
        with self.session_factory() as db:
            db.add_all([
                User(username="alice", password_hash=get_password_hash("secret"), role="user"),
                User(username="bob", password_hash=get_password_hash("secret"), role="user"),
                User(username="admin", password_hash=get_password_hash("secret"), role="admin"),
            ])
            db.commit()
        self.alice = self._token("alice")
        self.bob = self._token("bob")
        self.admin = self._token("admin")

    def tearDown(self):
        self.client.close()
        self.app.dependency_overrides.clear()
        self.engine.dispose()
        self.temp.cleanup()

    def _token(self, name):
        response = self.client.post("/auth/login", json={"username": name, "password": "secret"})
        self.assertEqual(response.status_code, 200, response.text)
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    def test_invite_only_registration_and_fixed_role(self):
        response = self.client.post("/auth/register", json={
            "username": "eve", "password": "secret", "role": "admin", "invite_code": "fake"})
        self.assertEqual(response.status_code, 422)
        response = self.client.post("/auth/register", json={"username": "eve", "password": "secret"})
        self.assertEqual(response.status_code, 422)
        response = self.client.post("/auth/register", json={
            "username": "eve", "password": "secret", "invite_code": "fake"})
        self.assertEqual(response.status_code, 403)

        response = self.client.post("/admin/invitations", headers=self.admin, json={"max_uses": 1})
        self.assertEqual(response.status_code, 200, response.text)
        code = response.json()["invite_code"]
        payload = {"username": "eve", "password": "secret", "invite_code": code}
        response = self.client.post("/auth/register", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["role"], "user")
        payload["username"] = "mallory"
        self.assertEqual(self.client.post("/auth/register", json=payload).status_code, 403)

    def test_private_knowledge_and_same_name_documents(self):
        a = self.client.post("/knowledges", headers=self.alice, json={"name": "A"})
        b = self.client.post("/knowledges", headers=self.bob, json={"name": "B"})
        self.assertEqual((a.status_code, b.status_code), (201, 201))
        a_id, b_id = a.json()["id"], b.json()["id"]
        for method, url in [
            (self.client.get, f"/knowledges/{b_id}"),
            (self.client.get, f"/knowledges/{b_id}/documents"),
            (self.client.delete, f"/knowledges/{b_id}"),
        ]:
            self.assertEqual(method(url, headers=self.alice).status_code, 404)
        self.assertEqual(len(self.client.get("/knowledges", headers=self.alice).json()["knowledges"]), 1)

        with self.session_factory() as db:
            db.add_all([
                KnowledgeDocument(id="a-doc", knowledge_id=a_id, filename="same.pdf",
                    storage_key="a.pdf", file_type=".pdf", status="ready"),
                KnowledgeDocument(id="b-doc", knowledge_id=b_id, filename="same.pdf",
                    storage_key="b.pdf", file_type=".pdf", status="ready"),
            ])
            db.commit()
        documents = self.client.get(f"/knowledges/{a_id}/documents", headers=self.alice).json()["documents"]
        self.assertEqual([item["id"] for item in documents], ["a-doc"])
        self.assertEqual(self.client.get(f"/knowledges/{a_id}/documents/b-doc", headers=self.alice).status_code, 404)
        self.assertEqual(self.client.get(f"/knowledges/{b_id}/documents/b-doc", headers=self.alice).status_code, 404)

    def test_upload_same_filename_uses_distinct_private_storage(self):
        from backend.routers import knowledges
        from backend.services import knowledge_service

        a_id = self.client.post("/knowledges", headers=self.alice, json={"name": "A"}).json()["id"]
        b_id = self.client.post("/knowledges", headers=self.bob, json={"name": "B"}).json()["id"]
        private_dir = Path(self.temp.name) / "uploads"
        with (patch.object(knowledge_service, "STORAGE_DIR", private_dir),
              patch.object(knowledges, "process_document")):
            upload = lambda kid, headers: self.client.post(
                f"/knowledges/{kid}/documents", headers=headers,
                files={"file": ("same.pdf", b"%PDF-1.4\nprivate", "application/pdf")})
            a = upload(a_id, self.alice)
            b = upload(b_id, self.bob)
            self.assertEqual((a.status_code, b.status_code), (202, 202))
            self.assertNotEqual(a.json()["id"], b.json()["id"])
            self.assertEqual(len(list(private_dir.iterdir())), 2)
            self.assertEqual(upload(b_id, self.alice).status_code, 404)

    def test_chat_receives_authenticated_owner_not_payload_owner(self):
        from backend.routers import chat
        with self.session_factory() as db:
            alice_id = db.query(User.id).filter(User.username == "alice").scalar()
        with patch.object(chat, "chat_with_agent", return_value={"response": "ok", "rag_trace": None}) as agent:
            response = self.client.post("/chat", headers=self.alice,
                json={"message": "hi", "owner_id": 999999})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(agent.call_args.args[2], alice_id)

    def test_removed_global_documents_endpoint_is_json_404(self):
        response = self.client.get("/documents", headers=self.alice)
        self.assertEqual(response.status_code, 404)
        self.assertTrue(response.headers["content-type"].startswith("application/json"))

    def test_retrieval_filters_hybrid_and_dense_fallback(self):
        from backend.rag import retrieval

        with self.session_factory() as db:
            alice = db.query(User).filter(User.username == "alice").one()
            bob = db.query(User).filter(User.username == "bob").one()
            db.add_all([
                KnowledgeBase(id="a-kb", owner_id=alice.id, name="A"),
                KnowledgeBase(id="b-kb", owner_id=bob.id, name="B"),
            ])
            db.add_all([
                KnowledgeDocument(id="a-doc", knowledge_id="a-kb", filename="same.pdf",
                    storage_key="a.pdf", file_type=".pdf", status="ready"),
                KnowledgeDocument(id="b-doc", knowledge_id="b-kb", filename="same.pdf",
                    storage_key="b.pdf", file_type=".pdf", status="ready"),
            ])
            db.commit()
            alice_id, bob_id = alice.id, bob.id

        own = {"document_id": "a-doc", "knowledge_id": "a-kb", "text": "Alice", "chunk_id": "a"}
        foreign = {"document_id": "b-doc", "knowledge_id": "b-kb", "text": "Bob", "chunk_id": "b"}
        with (patch.object(retrieval, "SessionLocal", self.session_factory),
              patch.object(retrieval._embedding_service, "get_embeddings", return_value=[[0.1]]),
              patch.object(retrieval._embedding_service, "get_sparse_embedding", return_value={1: 1.0}),
              patch.object(retrieval, "rerank_documents", side_effect=lambda query, docs, top_k: (docs, {})),
              patch.object(retrieval, "auto_merge_documents", side_effect=lambda docs, top_k, allowed_document_ids: (docs, {}))):
            with patch.object(retrieval._milvus_manager, "hybrid_retrieve", return_value=[own, foreign]) as hybrid:
                result = retrieval.retrieve_documents("same", owner_id=alice_id)
                self.assertEqual(result["docs"], [own])
                self.assertIn('document_id in ["a-doc"]', hybrid.call_args.kwargs["filter_expr"])
            with (patch.object(retrieval._milvus_manager, "hybrid_retrieve", side_effect=RuntimeError("down")),
                  patch.object(retrieval._milvus_manager, "dense_retrieve", return_value=[foreign, own]) as dense):
                result = retrieval.retrieve_documents("same", owner_id=alice_id)
                self.assertEqual(result["docs"], [own])
                self.assertIn('document_id in ["a-doc"]', dense.call_args.kwargs["filter_expr"])
            with self.assertRaises(HTTPException):
                retrieval.retrieve_documents("same", owner_id=bob_id, knowledge_ids=["a-kb"])
            with self.assertRaises(ValueError):
                retrieval.retrieve_documents("same", owner_id=0)


if __name__ == "__main__":
    unittest.main()
