import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.models import CallAudit, User
from backend.observability import request_id
from backend.routers.operations import operations
from backend.services.call_audit import prune_expired_audits, record_call


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


if __name__ == "__main__":
    unittest.main()
