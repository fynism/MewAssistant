"""Upgrade a deployed schema without changing existing passwords or ownership."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError


class GitHubMigrationTests(unittest.TestCase):
    def test_upgrade_and_rollback_preserve_existing_accounts(self):
        with tempfile.TemporaryDirectory() as tmp:
            url = f"sqlite:///{Path(tmp) / 'migration.sqlite'}"
            env = dict(os.environ, DATABASE_URL=url, LOG_SQL="false", GITHUB_OAUTH_ENABLED="false")

            def migrate(*args):
                result = subprocess.run([sys.executable, "-m", "alembic", *args], env=env,
                                        capture_output=True, text=True, encoding="utf-8", errors="replace")
                self.assertEqual(result.returncode, 0, result.stderr)

            migrate("upgrade", "0008_document_cleaning")
            engine = create_engine(url)
            try:
                # 0001 uses current models; remove this release's field to represent
                # a real previously deployed 0008 database.
                with engine.begin() as db:
                    db.execute(text("DROP INDEX uq_users_github_id"))
                    db.execute(text("ALTER TABLE users DROP COLUMN github_id"))
                with engine.begin() as db:
                    for id, name in ((1, "admin"), (2, "legacy")):
                        db.execute(text("INSERT INTO users (id,username,password_hash,role,created_at) VALUES (:id,:name,'preserved-hash','admin',CURRENT_TIMESTAMP)"), {"id": id, "name": name})
                migrate("upgrade", "head")
                with engine.begin() as db:
                    self.assertEqual(db.execute(text("SELECT github_id FROM users WHERE id=1")).scalar(), None)
                    self.assertEqual(db.execute(text("SELECT password_hash FROM users WHERE id=1")).scalar(), "preserved-hash")
                    db.execute(text("UPDATE users SET github_id='123' WHERE id=2"))
                with self.assertRaises(IntegrityError), engine.begin() as db:
                    db.execute(text("UPDATE users SET github_id='123' WHERE id=1"))
                migrate("downgrade", "0008_document_cleaning")
                self.assertNotIn("github_id", {column["name"] for column in inspect(engine).get_columns("users")})
                with engine.connect() as db:
                    self.assertEqual(db.execute(text("SELECT username,password_hash,role FROM users WHERE id=1")).one(),
                                     ("admin", "preserved-hash", "admin"))
                    self.assertEqual(db.execute(text("SELECT COUNT(*) FROM users")).scalar(), 2)
                migrate("upgrade", "head")
                from backend.infrastructure.database import session as database
                from unittest.mock import patch
                with patch.object(database, "engine", engine):
                    database.init_db()
            finally:
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
