import asyncio
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.models import KnowledgeBase, KnowledgeDocument, User
from backend.workers import maintenance


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self.temp.name) / 'recovery.db'}")
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.factory() as db:
            db.add_all([User(id=1, username="alice", password_hash="x"),
                        KnowledgeBase(id="kb", owner_id=1, name="KB"),
                        KnowledgeDocument(id="doc", knowledge_id="kb", filename="a.pdf",
                                          storage_key="a.pdf", file_type=".pdf", status="processing")])
            db.commit()

    def tearDown(self):
        self.engine.dispose()
        self.temp.cleanup()

    def test_startup_requeues_interrupted_job_and_dispatches_it(self):
        completed = threading.Event()

        def process(document_id):
            with self.factory() as db:
                item = db.get(KnowledgeDocument, document_id)
                item.status = "ready"
                db.commit()
            completed.set()

        async def run():
            task = asyncio.create_task(maintenance.run_document_maintenance())
            try:
                self.assertTrue(await asyncio.to_thread(completed.wait, 3))
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        with (patch.object(maintenance, "SessionLocal", self.factory),
              patch("backend.services.knowledge_service.SessionLocal", self.factory),
              patch.object(maintenance, "process_document", process),
              patch.object(maintenance, "prune_expired_audits", lambda: 0),
              patch.object(maintenance, "settings", replace(maintenance.settings,
                  document_scan_interval_seconds=0.2, max_document_processing=1))):
            asyncio.run(run())
        with self.factory() as db:
            self.assertEqual(db.get(KnowledgeDocument, "doc").status, "ready")


if __name__ == "__main__":
    unittest.main()
