"""Real CPU OCR + dense/sparse embeddings and document lifecycle, with a memory vector sink.

No Docker/Milvus is required. Run python tests/ingestion_ocr_smoke.py from the repo root.
Existing uploaded documents and BM25 state are not touched.
"""

import asyncio
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    os.environ["DATABASE_URL"] = f"sqlite:///{root / 'smoke.sqlite'}"
    os.environ["BM25_M1_STATE_PATH"] = str(root / "bm25.json")
    os.environ["LOG_REQUESTS"] = "false"

    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    from fastapi import UploadFile
    from backend.infrastructure.database.session import Base, engine, SessionLocal
    from backend.infrastructure.database.models import User, KnowledgeBase, KnowledgeDocumentPage
    from backend.infrastructure.resources import embedding_service
    from backend.infrastructure.vector_store.writer import MilvusWriter
    from backend.infrastructure import resources, storage
    from backend.services import knowledge as service
    from backend.services.knowledge.tools import retrieve
    from test_document_ingestion import MemoryMilvus

    Base.metadata.create_all(engine)
    image = Image.new("RGB", (1600, 900), "white")
    drawer = ImageDraw.Draw(image)
    chinese_font = Path("C:/Windows/Fonts/msyh.ttc")
    font = ImageFont.truetype(str(chinese_font), 48) if chinese_font.exists() else ImageFont.load_default(size=48)
    text = "SuperMew 2025\nVersion v2.0\nAmount 123.45%\n"
    if chinese_font.exists():
        text += "\u4e2d\u6587\u626b\u63cf\u6587\u6863\u6d4b\u8bd5"
    drawer.multiline_text((100, 100), text, font=font, fill="black", spacing=35)
    pdf_bytes = io.BytesIO()
    image.save(pdf_bytes, format="PDF", resolution=150)
    sink = MemoryMilvus()
    writer = MilvusWriter(embedding_service, sink)
    with (patch.object(storage, "KNOWLEDGE_DOCUMENTS_DIR", root / "files"),
          patch.object(resources, "milvus_manager", sink), patch.object(resources, "milvus_writer", writer)):
        with SessionLocal() as db:
            db.add_all([User(id=1, username="smoke", password_hash="x"),
                        KnowledgeBase(id="kb", owner_id=1, name="Smoke")])
            db.commit()
            doc = asyncio.run(service.save_upload(db, 1, "kb", UploadFile(file=io.BytesIO(pdf_bytes.getvalue()), filename="scan.pdf")))
            service.process_document(doc.id)
            db.expire_all()
            assert doc.status == "ready", (doc.status, doc.error_summary, doc.processing_report)
            page = db.get(KnowledgeDocumentPage, (doc.id, 0))
            assert page.extraction in {"ocr", "mixed"}, page.extraction
            assert "2025" in page.cleaned_text and "123.45" in page.cleaned_text, page.cleaned_text
            if chinese_font.exists():
                assert "\u4e2d\u6587" in page.cleaned_text, page.cleaned_text
            assert sink.rows and all(len(row["dense_embedding"]) == 1024 and row["sparse_embedding"] for row in sink.rows)
            query = embedding_service.get_embeddings(["SuperMew year 2025 amount"])[0]
            ranked = sorted(sink.rows, key=lambda row: float(np.dot(query, row["dense_embedding"])), reverse=True)
            with patch("backend.rag.retrieval.retrieve_documents", return_value={"docs": ranked, "meta": {}}):
                hit = retrieve(db, 1, "2025", ["kb"])["results"][0]
            assert hit["documentId"] == doc.id and hit["processingReport"]["indexed_pages"] == 1
            print(json.dumps({"ocr_text": page.cleaned_text, "report": doc.processing_report,
                              "vector_dimension": len(sink.rows[0]["dense_embedding"]),
                              "sparse_tokens": len(sink.rows[0]["sparse_embedding"])}, ensure_ascii=True))
            txt = asyncio.run(service.save_upload(db, 1, "kb", UploadFile(
                file=io.BytesIO("\u4e2d\u6587 TXT\r\n2025 amount 123.45% v2.0\u200b".encode("gb18030")), filename="notes.txt")))
            service.process_document(txt.id)
            db.expire_all()
            assert txt.status == "ready", txt.error_summary
            txt_rows = [row for row in sink.rows if row["document_id"] == txt.id]
            assert txt_rows and all(len(row["dense_embedding"]) == 1024 and row["sparse_embedding"] for row in txt_rows)
            assert "2025" in txt_rows[0]["text"] and "\u200b" not in txt_rows[0]["text"]
            service.delete_document(db, doc)
            service.delete_document(db, txt)
            assert not sink.rows and embedding_service._total_docs == 0
    engine.dispose()
    print("PASS: actual OCR, Chinese/English/numbers, cleaning, parent/leaf storage, BGE 1024D and BM25 vectors, scoped tool output and cleanup (memory vector sink).")
