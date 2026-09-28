import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.auth import get_current_user, get_db
from backend.models import CallAudit, User
from backend.observability import request_id
from backend.routers.operations import operations
from backend.routers.operations import router as operations_router
from backend.services.call_audit import prune_expired_audits, record_call
from backend.services.api_keys import create_key


class CallAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self.temp.name) / 'audit.db'}")
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.factory() as db:
            db.add(User(id=1, username="admin", password_hash="x", role="admin"))
            db.commit()

    def tearDown(self):
        self.engine.dispose()
        self.temp.cleanup()

    def test_minimal_record_dashboard_and_retention(self):
        with patch("backend.services.call_audit.SessionLocal", self.factory):
            token = request_id.set("req-123")
            try:
                record_call(1, "key-id", "retrieve", "success", 24)
                record_call(1, "key-id", "retrieve", "service_error", 41)
            finally:
                request_id.reset(token)
            with self.factory() as db:
                old = CallAudit(owner_id=1, key_id=None, operation="old",
                                result_category="success", request_id="old", duration_ms=1,
                                created_at=datetime.utcnow() - timedelta(days=91))
                db.add(old)
                db.commit()
                dashboard = operations(_=db.get(User, 1), db=db)
                self.assertEqual(dashboard["calls"], 2)
                self.assertEqual(dashboard["errors"], 1)
                self.assertEqual(dashboard["recentFailures"][0]["requestId"], "req-123")
                self.assertEqual(prune_expired_audits(), 1)
                self.assertEqual(db.query(CallAudit).count(), 2)

    def test_operations_endpoint_requires_admin(self):
        app = FastAPI()
        app.include_router(operations_router)

        def db_session():
            with self.factory() as db:
                yield db

        app.dependency_overrides[get_db] = db_session
        app.dependency_overrides[get_current_user] = lambda: User(id=2, username="user", role="user")
        with TestClient(app) as client:
            self.assertEqual(client.get("/admin/operations").status_code, 403)
            app.dependency_overrides[get_current_user] = lambda: User(id=1, username="admin", role="admin")
            self.assertEqual(client.get("/admin/operations").status_code, 200)

    def test_mcp_tool_call_persists_only_metadata(self):
        from backend.mcp_knowledge import _invoke
        from backend.services.knowledge_tools import list_knowledges

        with self.factory() as db:
            _, key = create_key(db, 1, "Codex")
        token = request_id.set("mcp-req")
        try:
            with (patch("backend.mcp_knowledge.SessionLocal", self.factory),
                  patch("backend.services.call_audit.SessionLocal", self.factory)):
                result = _invoke({"authorization": "Bearer " + key}, list_knowledges)
        finally:
            request_id.reset(token)
        self.assertEqual(result["items"], [])
        with self.factory() as db:
            audit = db.query(CallAudit).one()
            self.assertEqual((audit.operation, audit.result_category, audit.request_id),
                             ("listKnowledges", "success", "mcp-req"))
            self.assertEqual(audit.owner_id, 1)
            self.assertNotIn(key, repr(audit.__dict__))


if __name__ == "__main__":
    unittest.main()
