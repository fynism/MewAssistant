"""PostgreSQL concurrency smoke in a disposable schema; never touches app tables."""

import asyncio
import io
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import uuid4

from fastapi import HTTPException, UploadFile
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.core.config import settings


def main() -> None:
    schema = "m4_smoke_" + uuid4().hex[:12]
    admin_engine = create_engine(settings.database_url)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    try:
        base_url = make_url(settings.database_url)
        scoped_url = base_url.set(query={**base_url.query, "options": f"-csearch_path={schema}"})
        env = dict(os.environ, DATABASE_URL=scoped_url.render_as_string(hide_password=False))
        migrated = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                                  env=env, capture_output=True, text=True, timeout=120)
        if migrated.returncode:
            raise RuntimeError("PostgreSQL migration failed:\n" + migrated.stderr[-2500:])

        engine = create_engine(scoped_url)
        try:
            from backend.models import KnowledgeBase, KnowledgeDocument, User
            from backend.services import knowledge_service

            factory = sessionmaker(bind=engine, expire_on_commit=False)
            with factory() as db:
                db.add(User(id=1, username="quota-user", password_hash="x"))
                db.flush()
                db.add_all([KnowledgeBase(id="kb-1", owner_id=1, name="One"),
                            KnowledgeBase(id="kb-2", owner_id=1, name="Two")])
                db.commit()

            with tempfile.TemporaryDirectory(prefix="supermew-m4-files-") as tmp:
                old_storage = knowledge_service.STORAGE_DIR
                old_settings = knowledge_service.settings
                try:
                    from dataclasses import replace
                    knowledge_service.STORAGE_DIR = Path(tmp)
                    knowledge_service.settings = replace(old_settings, max_user_documents=1)
                    gate = Barrier(2)

                    def upload(knowledge_id: str):
                        gate.wait(timeout=10)
                        with factory() as db:
                            file = UploadFile(file=io.BytesIO(b"%PDF-1.4\nprivate"), filename="same.pdf")
                            try:
                                asyncio.run(knowledge_service.save_upload(db, 1, knowledge_id, file))
                                return 202
                            except HTTPException as exc:
                                return exc.status_code

                    with ThreadPoolExecutor(max_workers=2) as pool:
                        statuses = list(pool.map(upload, ("kb-1", "kb-2")))
                    assert sorted(statuses) == [202, 413], statuses
                    with factory() as db:
                        assert db.query(KnowledgeDocument).count() == 1
                    print("PostgreSQL M4 concurrency passed; one upload accepted, one quota rejection")
                finally:
                    knowledge_service.STORAGE_DIR = old_storage
                    knowledge_service.settings = old_settings
        finally:
            engine.dispose()
    finally:
        if not schema.startswith("m4_smoke_"):
            raise RuntimeError("Unsafe test schema name")
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


if __name__ == "__main__":
    main()
