"""Opt-in full ingest smoke using temporary SQLite, Milvus and a generated DOCX."""

import asyncio
import io
import os
import tempfile
import zipfile
from pathlib import Path
from uuid import uuid4

with tempfile.TemporaryDirectory() as temp_dir:
    root = Path(temp_dir)
    collection = "m1_ingest_" + uuid4().hex[:12]
    os.environ["DATABASE_URL"] = f"sqlite:///{root / 'm1.db'}"
    os.environ["MILVUS_M1_COLLECTION"] = collection
    os.environ["BM25_M1_STATE_PATH"] = str(root / "bm25.json")

    from fastapi import UploadFile
    from backend.database import Base, SessionLocal, engine
    from backend.milvus_client import MilvusManager
    from backend.models import KnowledgeBase, KnowledgeDocument, User
    from backend.rag.retrieval import retrieve_documents
    from backend.services import knowledge_service

    def make_docx(text: str) -> bytes:
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
                <w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>""")
        return stream.getvalue()

    Base.metadata.create_all(engine)
    knowledge_service.STORAGE_DIR = root / "files"
    db = SessionLocal()
    try:
        db.add_all([
            User(username="smoke_alice", password_hash="x", role="user"),
            User(username="smoke_bob", password_hash="x", role="user"),
        ])
        db.commit()
        alice, bob = db.query(User).order_by(User.id).all()
        db.add_all([
            KnowledgeBase(id=str(uuid4()), owner_id=alice.id, name="smoke A"),
            KnowledgeBase(id=str(uuid4()), owner_id=bob.id, name="smoke B"),
        ])
        db.commit()
        kb_a = db.query(KnowledgeBase).filter(KnowledgeBase.owner_id == alice.id).one()
        kb_b = db.query(KnowledgeBase).filter(KnowledgeBase.owner_id == bob.id).one()
        text = "Only Alice knows the sapphire dragon password. " * 15
        upload = UploadFile(file=io.BytesIO(make_docx(text)), filename="same.docx")
        document = asyncio.run(knowledge_service.save_upload(db, alice.id, kb_a.id, upload))
        upload_b = UploadFile(file=io.BytesIO(make_docx(
            "Only Bob knows the emerald tiger password. " * 15)), filename="same.docx")
        document_b = asyncio.run(knowledge_service.save_upload(db, bob.id, kb_b.id, upload_b))
        knowledge_service.process_document(document.id)
        knowledge_service.process_document(document_b.id)
        db.expire_all()
        status = db.query(KnowledgeDocument).filter(KnowledgeDocument.id == document.id).one().status
        assert status == "ready", status
        status_b = db.query(KnowledgeDocument).filter(KnowledgeDocument.id == document_b.id).one().status
        assert status_b == "ready", status_b
        own = retrieve_documents("sapphire dragon password", owner_id=alice.id)
        other = retrieve_documents("sapphire dragon password", owner_id=bob.id)
        assert own["docs"] and all(d["document_id"] == document.id for d in own["docs"]), own
        assert other["docs"] and all(d["document_id"] == document_b.id for d in other["docs"]), other
        knowledge_service.delete_document(db, document)
        after_delete = retrieve_documents("sapphire dragon password", owner_id=alice.id)
        still_b = retrieve_documents("emerald tiger password", owner_id=bob.id)
        assert after_delete["docs"] == [], after_delete
        assert still_b["docs"] and all(d["document_id"] == document_b.id for d in still_b["docs"]), still_b
        print("M1 full ingest, same-name isolation and targeted delete smoke passed")
    finally:
        db.close()
        MilvusManager(collection_name=collection).drop_collection()
        engine.dispose()
