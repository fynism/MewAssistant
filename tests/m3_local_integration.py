"""Temporary migrated SQLite + real HTTP + official MCP SDK integration smoke."""

import asyncio
import io
import os
import socket
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from urllib.request import urlopen
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


def make_docx(text):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("[Content_Types].xml", """<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>""")
        archive.writestr("_rels/.rels", """<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>""")
        archive.writestr("word/document.xml", f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>')
    return stream.getvalue()


async def call_tools(url, key, expected_id, denied_id, query="test", expected_text=None):
    async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {key}"}) as http:
        async with streamable_http_client(url, http_client=http) as streams:
            async with ClientSession(*streams) as session:
                negotiated = await session.initialize()
                tools = await session.list_tools()
                assert {item.name for item in tools.tools} == {"listKnowledges", "retrieve"}
                listed = await session.call_tool("listKnowledges", {})
                assert [item["id"] for item in listed.structured_content["items"]] == [expected_id]
                own = await session.call_tool("retrieve", {"query": query, "knowledgeIds": [expected_id]})
                if expected_text:
                    assert own.structured_content["status"] == "ok", own
                    assert all(item["knowledgeId"] == expected_id for item in own.structured_content["results"])
                    assert expected_text in own.structured_content["results"][0]["text"]
                else:
                    assert own.structured_content["status"] == "no_documents"
                denied = await session.call_tool("retrieve", {"query": "test", "knowledgeIds": [denied_id]})
                assert denied.is_error
                return str(negotiated.protocol_version)


def main():
    with tempfile.TemporaryDirectory(prefix="supermew-m3-") as tmp:
        root = Path(tmp)
        collection = "m3_integration_" + uuid4().hex[:12]
        use_milvus = "--milvus" in sys.argv
        env = os.environ.copy()
        env.update({
            "DATABASE_URL": f"sqlite:///{(root / 'm3.sqlite').as_posix()}",
            "MCP_EXTERNAL_ENABLED": "true",
            "MCP_ALLOWED_HOSTS": "127.0.0.1:*,localhost:*",
            "MCP_ALLOWED_ORIGINS": "",
            "MCP_PUBLIC_BASE_URL": "",
            "LOG_REQUESTS": "false",
        })
        if use_milvus:
            env["MILVUS_M1_COLLECTION"] = collection
            env["BM25_M1_STATE_PATH"] = str(root / "bm25.json")
        migrated = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                                  env=env, capture_output=True, text=True, timeout=120)
        if migrated.returncode:
            raise RuntimeError("Migration failed:\n" + migrated.stderr[-3000:])
        os.environ.update({name: env[name] for name in ("DATABASE_URL", "MCP_EXTERNAL_ENABLED",
                          "MCP_ALLOWED_HOSTS", "MCP_ALLOWED_ORIGINS", "MCP_PUBLIC_BASE_URL")})
        if use_milvus:
            os.environ["MILVUS_M1_COLLECTION"] = collection
            os.environ["BM25_M1_STATE_PATH"] = env["BM25_M1_STATE_PATH"]
        from backend.database import SessionLocal, engine
        from backend.models import KnowledgeBase, PersonalApiKey, User
        from backend.services.api_keys import create_key
        with SessionLocal() as db:
            db.add_all([User(id=1, username="m3-alice", password_hash="x", role="user"),
                        User(id=2, username="m3-bob", password_hash="x", role="user"),
                        KnowledgeBase(id="m3-alice-kb", owner_id=1, name="Alice"),
                        KnowledgeBase(id="m3-bob-kb", owner_id=2, name="Bob")])
            db.commit()
            alice_item, alice_key = create_key(db, 1, "Alice SDK")
            _, bob_key = create_key(db, 2, "Bob SDK")
            alice_key_id = alice_item.id
            if use_milvus:
                from fastapi import UploadFile
                from backend.models import KnowledgeDocument
                from backend.services import knowledge_service
                knowledge_service.STORAGE_DIR = root / "files"
                for owner_id, knowledge_id, phrase in (
                    (1, "m3-alice-kb", "sapphire dragon password"),
                    (2, "m3-bob-kb", "emerald tiger password"),
                ):
                    upload = UploadFile(file=io.BytesIO(make_docx((phrase + ". ") * 15)),
                                        filename="same.docx")
                    document = asyncio.run(knowledge_service.save_upload(db, owner_id, knowledge_id, upload))
                    knowledge_service.process_document(document.id)
                    db.expire_all()
                    assert db.get(KnowledgeDocument, document.id).status == "ready"
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        server_log = root / "server.log"
        with server_log.open("w", encoding="utf-8") as log:
            process = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.app:app",
                                        "--host", "127.0.0.1", "--port", str(port)],
                                       env=env, stdout=log, stderr=subprocess.STDOUT,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                base = f"http://127.0.0.1:{port}"
                for _ in range(120):
                    if process.poll() is not None:
                        raise RuntimeError("Server exited:\n" + server_log.read_text(encoding="utf-8")[-3000:])
                    try:
                        with urlopen(base + "/platform/services/knowledge", timeout=1) as response:
                            if response.status == 200:
                                break
                    except Exception:
                        time.sleep(1)
                else:
                    raise TimeoutError("Server did not start")
                endpoint = base + "/mcp/knowledge"
                alice_version = asyncio.run(asyncio.wait_for(
                    call_tools(endpoint, alice_key, "m3-alice-kb", "m3-bob-kb",
                               "sapphire dragon password" if use_milvus else "test",
                               "sapphire dragon" if use_milvus else None), timeout=60))
                bob_version = asyncio.run(asyncio.wait_for(
                    call_tools(endpoint, bob_key, "m3-bob-kb", "m3-alice-kb",
                               "emerald tiger password" if use_milvus else "test",
                               "emerald tiger" if use_milvus else None), timeout=60))
                if use_milvus:
                    from backend.models import KnowledgeDocument
                    from backend.services import knowledge_service
                    with SessionLocal() as db:
                        item = db.query(KnowledgeDocument).filter(
                            KnowledgeDocument.knowledge_id == "m3-alice-kb").one()
                        knowledge_service.delete_document(db, item)
                    asyncio.run(asyncio.wait_for(
                        call_tools(endpoint, alice_key, "m3-alice-kb", "m3-bob-kb"), timeout=60))
                with SessionLocal() as db:
                    item = db.get(PersonalApiKey, alice_key_id)
                    from datetime import datetime
                    item.revoked_at = datetime.utcnow()
                    db.commit()
                with httpx2.Client() as client:
                    rejected = client.post(endpoint, headers={"Authorization": f"Bearer {alice_key}"},
                        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
                    assert rejected.status_code == 401, rejected.status_code
                print("local SDK integration passed; protocols:", alice_version, bob_version,
                      "Milvus:" , use_milvus)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
                engine.dispose()
                if use_milvus:
                    from backend.milvus_client import MilvusManager
                    MilvusManager(collection_name=collection).drop_collection()
                time.sleep(1)


if __name__ == "__main__":
    main()
