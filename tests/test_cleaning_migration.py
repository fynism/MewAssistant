import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine, inspect, text


class CleaningMigrationTests(unittest.TestCase):
    def test_upgrade_preserves_old_documents_and_downgrade_removes_only_new_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            url = f"sqlite:///{Path(tmp) / 'migration.sqlite'}"
            env = dict(os.environ, DATABASE_URL=url, LOG_SQL="false")
            def migrate(*args):
                result = subprocess.run([sys.executable, "-m", "alembic", *args], env=env,
                                        capture_output=True, text=True, encoding="utf-8", errors="replace")
                self.assertEqual(result.returncode, 0, result.stderr)
            migrate("upgrade", "0007_m4_call_audits")
            engine = create_engine(url)
            with engine.begin() as db:
                db.execute(text("INSERT INTO users (id,username,password_hash,role,created_at) VALUES (1,'a','x','user',CURRENT_TIMESTAMP)"))
                db.execute(text("INSERT INTO knowledge_bases (id,owner_id,name,description,status,created_at,updated_at) VALUES ('kb',1,'KB','','active',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
                db.execute(text("INSERT INTO knowledge_documents (id,knowledge_id,filename,storage_key,file_type,status,replacement_old_ready,size_bytes,created_at,updated_at) VALUES ('doc','kb','old.txt','old.txt','.txt','ready',0,10,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
            migrate("upgrade", "head")
            inspector = inspect(engine)
            self.assertTrue(inspector.has_table("knowledge_document_pages"))
            self.assertIn("processing_report", {c["name"] for c in inspector.get_columns("knowledge_documents")})
            with engine.connect() as db:
                self.assertEqual(db.execute(text("SELECT status FROM knowledge_documents WHERE id='doc'")).scalar(), "ready")
            migrate("downgrade", "0007_m4_call_audits")
            self.assertFalse(inspect(engine).has_table("knowledge_document_pages"))
            with engine.connect() as db:
                self.assertEqual(db.execute(text("SELECT filename FROM knowledge_documents WHERE id='doc'")).scalar(), "old.txt")
            migrate("upgrade", "head")
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
