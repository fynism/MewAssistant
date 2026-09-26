import importlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.models import KnowledgeBase, KnowledgeDocument, User
from backend.services.api_keys import create_key


class McpProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app_module = importlib.import_module("backend.app")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self.temp.name) / 'db.sqlite'}")
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.factory() as db:
            db.add_all([User(id=1, username="alice", password_hash="x", role="user"),
                        User(id=2, username="bob", password_hash="x", role="user"),
                        KnowledgeBase(id="alice-kb", owner_id=1, name="Alice KB"),
                        KnowledgeBase(id="bob-kb", owner_id=2, name="Bob KB"),
                        KnowledgeDocument(id="alice-doc", knowledge_id="alice-kb", filename="a.txt",
                                          storage_key="a.txt", file_type=".txt", status="ready")])
            db.commit()
            _, self.alice_key = create_key(db, 1, "Codex")
            _, self.bob_key = create_key(db, 2, "Codex")
        self.patches = [patch("backend.mcp_knowledge.SessionLocal", self.factory),
                        patch("backend.mcp_knowledge.settings", SimpleNamespace(
                            mcp_external_enabled=True, mcp_allowed_origins="")),
                        patch.object(self.app_module, "init_db", lambda: None)]
        for item in self.patches:
            item.start()
        self.client_context = TestClient(self.app_module.app)
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        for item in reversed(self.patches):
            item.stop()
        self.engine.dispose()
        self.temp.cleanup()

    def post(self, payload, key=None, version="2025-11-25", **headers):
        return self.client.post("/mcp/knowledge", json=payload, headers={
            "Authorization": "Bearer " + (key or self.alice_key),
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": version, **headers})

    def test_legacy_handshake_tools_notification_and_scope(self):
        initialized = self.post({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-11-25", "capabilities": {},
            "clientInfo": {"name": "m3-test", "version": "1"}}})
        self.assertEqual(initialized.status_code, 200, initialized.text)
        self.assertIn("result", initialized.json())
        notice = self.post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertEqual(notice.status_code, 202, notice.text)
        self.assertEqual(notice.content, b"")
        tools = self.post({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        self.assertEqual(tools.status_code, 200, tools.text)
        schemas = {item["name"]: item for item in tools.json()["result"]["tools"]}
        self.assertEqual(set(schemas), {"listKnowledges", "retrieve"})
        self.assertIn("knowledgeIds", schemas["retrieve"]["inputSchema"]["required"])
        self.assertTrue(schemas["retrieve"]["inputSchema"]["properties"]["knowledgeIds"]["uniqueItems"])
        self.assertEqual(schemas["retrieve"]["inputSchema"]["properties"]["knowledgeIds"]["minItems"], 1)
        self.assertIn("outputSchema", schemas["retrieve"])
        listed = self.post({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
            "name": "listKnowledges", "arguments": {}}})
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual([item["id"] for item in listed.json()["result"]["structuredContent"]["items"]], ["alice-kb"])
        self.assertIn("alice-kb", listed.json()["result"]["content"][0]["text"])
        denied = self.post({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {
            "name": "retrieve", "arguments": {"query": "secret", "knowledgeIds": ["bob-kb"]}}})
        self.assertEqual(denied.status_code, 200, denied.text)
        self.assertTrue(denied.json()["result"]["isError"])
        missing = self.post({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {
            "name": "retrieve", "arguments": {"query": "secret"}}})
        self.assertTrue(missing.json()["result"]["isError"])
        with patch("backend.rag.retrieval.retrieve_documents", return_value={"docs": [
            {"document_id": "alice-doc", "chunk_id": "c1", "text": "private Alice data", "page_number": 1}],
            "meta": {"retrieval_mode": "hybrid"}}):
            found = self.post({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {
                "name": "retrieve", "arguments": {"query": "Alice", "knowledgeIds": ["alice-kb"]}}})
        self.assertEqual(found.status_code, 200, found.text)
        self.assertEqual(found.json()["result"]["structuredContent"]["results"][0]["text"], "private Alice data")
        self.assertEqual(self.post({"jsonrpc": "2.0", "id": 6, "method": "tools/list", "params": {}},
                                   key=self.bob_key).json()["result"]["tools"][0]["name"], "listKnowledges")
        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        self.assertEqual(self.client.post("/mcp/knowledge", json=payload).status_code, 401)
        self.assertEqual(self.post(payload, key="wrong").status_code, 401)
        self.assertEqual(self.post(payload, Origin="https://evil.example").status_code, 403)
        self.assertEqual(self.post(payload, version="unsupported").status_code, 400)
        modern = {"jsonrpc": "2.0", "id": 8, "method": "tools/list", "params": {
            "_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
                      "io.modelcontextprotocol/clientInfo": {"name": "m3-test", "version": "1"},
                      "io.modelcontextprotocol/clientCapabilities": {}}}}
        modern_result = self.post(modern, version="2026-07-28", **{"Mcp-Method": "tools/list"})
        self.assertEqual(modern_result.status_code, 200, modern_result.text)


if __name__ == "__main__":
    unittest.main()
