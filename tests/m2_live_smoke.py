"""Opt-in M2 smoke against local PostgreSQL, Redis and Milvus.

Creates a disposable database and Milvus collection; run with
``$env:PYTHONPATH='.'; uv run python tests/m2_live_smoke.py``.
"""

import io
import os
import tempfile
import zipfile
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


def docx_bytes(content: str) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("[Content_Types].xml", """<?xml version="1.0"?>
            <Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
            <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
            <Default Extension="xml" ContentType="application/xml"/>
            <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
            </Types>""")
        archive.writestr("_rels/.rels", """<?xml version="1.0"?>
            <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
            <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
            </Relationships>""")
        archive.writestr("word/document.xml", f"""<?xml version="1.0"?>
            <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
            <w:body><w:p><w:r><w:t>{content}</w:t></w:r></w:p></w:body></w:document>""")
    return stream.getvalue()


def main() -> None:
    load_dotenv()
    source = make_url(os.environ.get("DATABASE_URL", "postgresql+psycopg2://postgres:postgres@localhost:5432/langchain_app"))
    if not source.drivername.startswith("postgresql"):
        raise RuntimeError("Live smoke requires PostgreSQL")
    suffix = uuid4().hex[:10]
    database_name = f"supermew_m2_{suffix}"
    collection_name = f"m2_live_{suffix}"
    admin_engine = create_engine(source.set(database="postgres"), isolation_level="AUTOCOMMIT")
    created = False
    with tempfile.TemporaryDirectory() as temp_name:
        temp = Path(temp_name)
        try:
            with admin_engine.connect() as connection:
                connection.execute(text(f"CREATE DATABASE {database_name}"))
            created = True
            os.environ["DATABASE_URL"] = source.set(database=database_name).render_as_string(hide_password=False)
            os.environ["MILVUS_M1_COLLECTION"] = collection_name
            os.environ["BM25_M1_STATE_PATH"] = str(temp / "bm25.json")
            os.environ["REDIS_KEY_PREFIX"] = f"m2_live_{suffix}"

            from alembic import command
            from alembic.config import Config
            command.upgrade(Config("alembic.ini"), "head")

            import redis
            from fastapi.testclient import TestClient
            from backend.app import create_app
            from backend.auth import get_password_hash
            from backend.database import SessionLocal, engine
            from backend.milvus_client import MilvusManager
            from backend.models import User
            from backend.services import knowledge_service

            assert redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0")).ping()
            knowledge_service.STORAGE_DIR = temp / "files"
            db = SessionLocal()
            try:
                db.add_all([
                    User(username="m2_alice", password_hash=get_password_hash("m2-password"), role="user"),
                    User(username="m2_bob", password_hash=get_password_hash("m2-password"), role="user"),
                ])
                db.commit()
            finally:
                db.close()

            def check(response, status=200):
                assert response.status_code == status, (response.status_code, response.text[:500])
                return response.json()

            with TestClient(create_app()) as client:
                for path in ["/", "/services/knowledge", "/account", "/workspace/knowledges", "/try"]:
                    assert client.get(path).status_code == 200, path
                assert client.get("/unlisted-page").status_code == 404
                assert client.post("/tools/debug/retrieve", json={"query": "x"}).status_code == 401
                tokens = {}
                for username in ("m2_alice", "m2_bob"):
                    login = check(client.post("/auth/login", json={"username": username, "password": "m2-password"}))
                    tokens[username] = {"Authorization": f"Bearer {login['access_token']}"}
                alice = tokens["m2_alice"]
                bob = tokens["m2_bob"]
                a1 = check(client.post("/knowledges", headers=alice, json={"name": "Alice A"}), 201)["id"]
                a2 = check(client.post("/knowledges", headers=alice, json={"name": "Alice B"}), 201)["id"]
                b1 = check(client.post("/knowledges", headers=bob, json={"name": "Bob A"}), 201)["id"]
                check(client.patch(f"/knowledges/{a2}", headers=alice, json={"name": "Alice B edited", "description": "Second scope"}))
                assert client.get(f"/knowledges/{b1}", headers=alice).status_code == 404

                contents = [
                    (alice, a1, "sapphire dragon archive " * 16),
                    (alice, a2, "copper moon observatory " * 16),
                    (bob, b1, "emerald tiger warehouse " * 16),
                ]
                document_ids = []
                for headers, kb_id, content in contents:
                    item = check(client.post(f"/knowledges/{kb_id}/documents", headers=headers,
                        files={"file": ("same.docx", docx_bytes(content), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}), 202)
                    document_ids.append(item["id"])
                    status = check(client.get(f"/knowledges/{kb_id}/documents/{item['id']}", headers=headers))["status"]
                    assert status == "ready", status
                assert len(set(document_ids)) == 3
                listed = check(client.post("/tools/debug/listKnowledges", headers=alice, json={"limit": 1}))
                assert len(listed["items"]) == 1 and listed["nextCursor"]
                listed2 = check(client.post("/tools/debug/listKnowledges", headers=alice,
                    json={"limit": 1, "cursor": listed["nextCursor"]}))
                assert {listed["items"][0]["id"], listed2["items"][0]["id"]} == {a1, a2}
                assert check(client.post("/tools/debug/retrieve", headers=alice,
                    json={"query": "sapphire dragon", "knowledgeIds": [], "topK": 5}))["status"] == "no_documents"
                assert client.post("/tools/debug/retrieve", headers=alice,
                    json={"query": "emerald tiger", "knowledgeIds": [a1, b1]}).status_code == 404
                hit = check(client.post("/tools/debug/retrieve", headers=alice,
                    json={"query": "sapphire dragon", "knowledgeIds": [a1], "topK": 5}))
                assert hit["status"] == "ok" and hit["results"]
                assert all(item["documentId"] == document_ids[0] and item["knowledgeId"] == a1
                    for item in hit["results"]), hit
                assert not any("emerald tiger" in item["text"] for item in hit["results"])

                replaced = check(client.put(f"/knowledges/{a1}/documents/{document_ids[0]}/content", headers=alice,
                    files={"file": ("same.docx", docx_bytes("silver comet library " * 16),
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}), 202)
                assert replaced["id"] == document_ids[0]
                assert check(client.get(f"/knowledges/{a1}/documents/{document_ids[0]}", headers=alice))["status"] == "ready"
                after = check(client.post("/tools/debug/retrieve", headers=alice,
                    json={"query": "silver comet", "knowledgeIds": [a1], "topK": 5}))
                assert after["status"] == "ok" and after["results"]
                assert all("sapphire dragon" not in item["text"] for item in after["results"])
                check(client.delete(f"/knowledges/{a1}/documents/{document_ids[0]}", headers=alice))
                assert check(client.post("/tools/debug/retrieve", headers=alice,
                    json={"query": "silver comet", "knowledgeIds": [a1]}))["status"] == "no_documents"
                assert check(client.post("/tools/debug/retrieve", headers=bob,
                    json={"query": "emerald tiger", "knowledgeIds": [b1]}))["status"] == "ok"
                check(client.delete(f"/knowledges/{a2}", headers=alice))
                assert client.get(f"/knowledges/{a2}", headers=alice).status_code == 404
            print("M2 PostgreSQL/Redis/Milvus live smoke passed")
        finally:
            if created:
                try:
                    from backend.milvus_client import MilvusManager
                    MilvusManager(collection_name=collection_name).drop_collection()
                except ImportError:
                    pass
                try:
                    from backend.database import engine
                    engine.dispose()
                except ImportError:
                    pass
                with admin_engine.connect() as connection:
                    connection.execute(text(f"DROP DATABASE {database_name} WITH (FORCE)"))
            admin_engine.dispose()


if __name__ == "__main__":
    main()
