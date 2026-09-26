import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.auth import create_access_token, get_db
from backend.database import Base
from backend.models import ChatMessage, ChatSession, KnowledgeBase, User


class M2ChatScopeTests(unittest.TestCase):
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
                KnowledgeBase(id="a", owner_id=1, name="A"),
                KnowledgeBase(id="b", owner_id=2, name="B"),
            ])
            db.commit()
        self.alice = {"Authorization": "Bearer " + create_access_token("alice", "user")}

    def tearDown(self):
        self.client.close()
        self.app.dependency_overrides.clear()
        self.engine.dispose()
        self.temp.cleanup()

    def test_sync_and_stream_reject_foreign_scope_before_agent(self):
        from backend.routers import chat
        payload = {"message": "question", "knowledge_ids": ["b"]}
        with (patch.object(chat, "chat_with_agent") as sync_agent,
              patch.object(chat, "chat_with_agent_stream") as stream_agent):
            self.assertEqual(self.client.post("/chat", headers=self.alice, json=payload).status_code, 404)
            self.assertEqual(self.client.post("/chat/stream", headers=self.alice, json=payload).status_code, 404)
            sync_agent.assert_not_called()
            stream_agent.assert_not_called()

    def test_explicit_empty_scope_stays_empty(self):
        from backend.routers import chat
        with patch.object(chat, "chat_with_agent",
                          return_value={"response": "empty", "knowledge_ids": []}) as agent:
            response = self.client.post("/chat", headers=self.alice,
                json={"message": "question", "knowledge_ids": []})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(agent.call_args.kwargs["knowledge_ids"], [])

    def test_history_keeps_each_turn_snapshot_and_legacy_flag(self):
        from backend.services import conversation_storage as module

        class Cache:
            def get_json(self, _key):
                return None

            def set_json(self, _key, _value):
                pass

            def delete(self, _key):
                pass

        storage = module.ConversationStorage()
        first = [HumanMessage(content="first"), AIMessage(content="answer one")]
        second = first + [HumanMessage(content="second"), AIMessage(content="answer two")]
        with (patch.object(module, "SessionLocal", self.factory),
              patch.object(module, "cache", Cache())):
            storage.save("alice", "new", first, metadata={"last_knowledge_ids": ["a"]},
                extra_message_data=[{"knowledge_ids": ["a"]}, {"knowledge_ids": ["a"]}])
            storage.save("alice", "new", second, metadata={"last_knowledge_ids": []},
                extra_message_data=[None, None, {"knowledge_ids": []}, {"knowledge_ids": []}])
            records = storage.get_session_messages("alice", "new")
            self.assertEqual([row["knowledge_ids"] for row in records], [["a"], ["a"], [], []])
            self.assertEqual(storage.get_session_metadata("alice", "new")["last_knowledge_ids"], [])
            with self.factory() as db:
                legacy = ChatSession(user_id=1, session_id="old", metadata_json={})
                db.add(legacy)
                db.flush()
                db.add(ChatMessage(session_ref_id=legacy.id, message_type="human", content="old"))
                db.commit()
            infos = {item["session_id"]: item for item in storage.list_session_infos("alice")}
            self.assertFalse(infos["new"]["legacy_scope_unknown"])
            self.assertTrue(infos["old"]["legacy_scope_unknown"])

    def test_initial_and_expansion_retrieval_use_same_scope(self):
        from backend import rag_pipeline
        state = {"owner_id": 1, "knowledge_ids": ["a"], "question": "question",
                 "expansion_type": "complex", "hypothetical_doc": "hyde",
                 "expanded_query": "step back", "rag_trace": {}}
        with patch.object(rag_pipeline, "retrieve_documents",
                          return_value={"docs": [], "meta": {}}) as retrieve:
            rag_pipeline.retrieve_initial(state)
            rag_pipeline.retrieve_expanded(state)
        self.assertEqual(retrieve.call_count, 3)
        self.assertTrue(all(call.kwargs["knowledge_ids"] == ["a"] for call in retrieve.call_args_list))


if __name__ == "__main__":
    unittest.main()
