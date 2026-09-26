"""Private knowledge and document lifecycle. Legacy documents are never read here."""

import ntpath
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.database import SessionLocal
from backend.dependencies import document_loader, embedding_service, milvus_manager, milvus_writer
from backend.models import KnowledgeBase, KnowledgeDocument, KnowledgeParentChunk

STORAGE_DIR = Path(__file__).resolve().parents[2] / "data" / "knowledge_documents"
ALLOWED_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx"}


def get_owned_knowledge(db: Session, owner_id: int, knowledge_id: str,
                        include_deleting: bool = False) -> KnowledgeBase:
    allowed_status = ["active", "deleting"] if include_deleting else ["active"]
    item = db.query(KnowledgeBase).filter(KnowledgeBase.id == knowledge_id,
                                          KnowledgeBase.owner_id == owner_id,
                                          KnowledgeBase.status.in_(allowed_status)).first()
    if item is None:
        raise HTTPException(status_code=404, detail="知识库不存在")
    return item


def get_owned_document(db: Session, owner_id: int, knowledge_id: str,
                       document_id: str, include_deleting: bool = False) -> KnowledgeDocument:
    get_owned_knowledge(db, owner_id, knowledge_id)
    query = db.query(KnowledgeDocument).filter(KnowledgeDocument.id == document_id,
        KnowledgeDocument.knowledge_id == knowledge_id)
    if not include_deleting:
        query = query.filter(KnowledgeDocument.status != "deleting")
    item = query.first()
    if item is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    return item


def visible_document_ids(db: Session, owner_id: int, knowledge_ids: list[str] | None = None) -> list[str]:
    query = db.query(KnowledgeDocument.id).join(KnowledgeBase,
        KnowledgeDocument.knowledge_id == KnowledgeBase.id).filter(
        KnowledgeBase.owner_id == owner_id, KnowledgeBase.status == "active",
        KnowledgeDocument.status == "ready")
    if knowledge_ids is not None:
        ids = list(set(knowledge_ids))
        if not ids:
            return []
        owned = {row[0] for row in db.query(KnowledgeBase.id).filter(
            KnowledgeBase.owner_id == owner_id, KnowledgeBase.status == "active",
            KnowledgeBase.id.in_(ids)).all()}
        if owned != set(ids):
            raise HTTPException(status_code=404, detail="知识库不存在")
        query = query.filter(KnowledgeDocument.knowledge_id.in_(ids))
    return [row[0] for row in query.all()]


def create_knowledge(db: Session, owner_id: int, name: str, description: str = "") -> KnowledgeBase:
    name = name.strip()
    if not name or len(name) > 200:
        raise HTTPException(status_code=400, detail="知识库名称长度必须为 1 到 200 字")
    item = KnowledgeBase(id=str(uuid4()), owner_id=owner_id, name=name, description=description.strip())
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def _verify_file(data: bytes, suffix: str) -> None:
    if not data or len(data) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="文件为空或超过上传大小限制")
    if suffix == ".pdf" and not data.startswith(b"%PDF-"):
        raise HTTPException(status_code=400, detail="PDF 文件格式无效")
    if suffix in {".docx", ".xlsx"} and not data.startswith(b"PK\x03\x04"):
        raise HTTPException(status_code=400, detail="Office 文件格式无效")
    if suffix in {".doc", ".xls"} and not data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        raise HTTPException(status_code=400, detail="Office 文件格式无效")


async def save_upload(db: Session, owner_id: int, knowledge_id: str, file: UploadFile) -> KnowledgeDocument:
    get_owned_knowledge(db, owner_id, knowledge_id)
    filename, suffix, data = await _read_upload(file)
    doc_id = str(uuid4())
    storage_key = doc_id + suffix
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    path = STORAGE_DIR / storage_key
    path.write_bytes(data)
    item = KnowledgeDocument(id=doc_id, knowledge_id=knowledge_id,
        filename=filename, storage_key=storage_key, file_type=suffix, status="pending")
    try:
        db.add(item)
        db.commit()
    except Exception:
        db.rollback()
        path.unlink(missing_ok=True)
        raise
    return item


async def _read_upload(file: UploadFile) -> tuple[str, str, bytes]:
    filename = ntpath.basename(file.filename or "").strip()
    suffix = Path(filename).suffix.lower()
    if not filename or filename in {".", ".."} or suffix not in ALLOWED_EXTENSIONS or "\x00" in filename:
        raise HTTPException(status_code=400, detail="仅支持 PDF、Word、Excel 文档")
    data = await file.read(settings.max_upload_bytes + 1)
    _verify_file(data, suffix)
    return filename[:255], suffix, data


async def replace_document(db: Session, owner_id: int, knowledge_id: str,
                           document_id: str, file: UploadFile) -> KnowledgeDocument:
    get_owned_knowledge(db, owner_id, knowledge_id)
    filename, suffix, data = await _read_upload(file)
    item = db.query(KnowledgeDocument).filter(
        KnowledgeDocument.id == document_id,
        KnowledgeDocument.knowledge_id == knowledge_id,
    ).with_for_update().first()
    if item is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    if item.status not in {"ready", "failed"}:
        raise HTTPException(status_code=409, detail="文件当前状态不允许替换")
    storage_key = str(uuid4()) + suffix
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    path = STORAGE_DIR / storage_key
    path.write_bytes(data)
    try:
        item.replacement_storage_key = storage_key
        item.replacement_filename = filename
        item.replacement_file_type = suffix
        item.replacement_old_ready = item.status == "ready"
        item.status = "replacing"
        item.error_summary = None
        item.updated_at = datetime.utcnow()
        db.commit()
    except Exception:
        db.rollback()
        path.unlink(missing_ok=True)
        raise
    return item


def _cleanup_vectors(db: Session, item: KnowledgeDocument, remove_bm25: bool = False) -> None:
    milvus_manager.init_collection()
    expr = f'document_id == "{item.id}"'
    rows = milvus_manager.query_all(filter_expr=expr, output_fields=["text"])
    milvus_manager.delete(expr)
    if rows and (remove_bm25 or item.status in {"ready", "deleting"}):
        embedding_service.increment_remove_documents([r.get("text", "") for r in rows])
    db.query(KnowledgeParentChunk).filter(KnowledgeParentChunk.document_id == item.id).delete()
    db.commit()


def process_document(document_id: str) -> None:
    db = SessionLocal()
    try:
        item = db.query(KnowledgeDocument).filter(KnowledgeDocument.id == document_id).with_for_update(skip_locked=True).first()
        if item is None or item.status not in {"pending", "failed", "replacing"}:
            return
        item.status = "processing"
        item.error_summary = None
        item.updated_at = datetime.utcnow()
        db.commit()
        try:
            if item.replacement_storage_key:
                _cleanup_vectors(db, item, remove_bm25=item.replacement_old_ready)
                old_storage_key = item.storage_key
                item.storage_key = item.replacement_storage_key
                item.filename = item.replacement_filename
                item.file_type = item.replacement_file_type
                item.replacement_storage_key = None
                item.replacement_filename = None
                item.replacement_file_type = None
                item.replacement_old_ready = False
                db.commit()
                (STORAGE_DIR / old_storage_key).unlink(missing_ok=True)
            else:
                _cleanup_vectors(db, item)
            path = STORAGE_DIR / item.storage_key
            chunks = document_loader.load_document(str(path), item.filename, item.id, item.knowledge_id)
            parents = [c for c in chunks if c["chunk_level"] in (1, 2)]
            leaves = [c for c in chunks if c["chunk_level"] == 3]
            if not leaves:
                raise ValueError("文件没有可检索内容")
            db.add_all([KnowledgeParentChunk(
                chunk_id=c["chunk_id"], knowledge_id=item.knowledge_id, document_id=item.id,
                text=c["text"], filename=item.filename, file_type=c["file_type"],
                page_number=c["page_number"], parent_chunk_id=c.get("parent_chunk_id", ""),
                root_chunk_id=c.get("root_chunk_id", ""), chunk_level=c["chunk_level"],
                chunk_idx=c["chunk_idx"]) for c in parents])
            db.commit()
            milvus_writer.write_documents(leaves)
            item.status = "ready"
            item.updated_at = datetime.utcnow()
            db.commit()
        except Exception:
            db.rollback()
            try:
                _cleanup_vectors(db, item, remove_bm25=item.replacement_old_ready)
            except Exception:
                db.rollback()
            item.status = "failed"
            item.error_summary = "处理失败；请重试或联系管理员"
            item.updated_at = datetime.utcnow()
            db.commit()
    finally:
        db.close()


def recover_pending_documents(include_stale_processing: bool = False) -> int:
    db = SessionLocal()
    try:
        if include_stale_processing:
            # Only run this mode after stopping the web process. Elapsed time
            # alone cannot prove an embedding job is no longer running.
            stale = datetime.utcnow() - timedelta(minutes=10)
            db.query(KnowledgeDocument).filter(
                KnowledgeDocument.status == "processing",
                KnowledgeDocument.updated_at < stale,
            ).update({"status": "pending"}, synchronize_session=False)
        db.commit()
        ids = [row[0] for row in db.query(KnowledgeDocument.id).filter(
            KnowledgeDocument.status.in_(["pending", "replacing"])).all()]
    finally:
        db.close()
    for document_id in ids:
        process_document(document_id)
    return len(ids)


def delete_document(db: Session, item: KnowledgeDocument) -> None:
    item = db.query(KnowledgeDocument).filter(
        KnowledgeDocument.id == item.id,
        KnowledgeDocument.knowledge_id == item.knowledge_id,
    ).with_for_update().first()
    if item is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    # Processing owns the index write. A deletion during that write could leave
    # vectors behind after the cleanup query has already run.
    if item.status == "processing":
        raise HTTPException(status_code=409, detail="文件正在处理，请稍后重试删除")
    item.status = "deleting"
    db.commit()
    _cleanup_vectors(db, item)
    (STORAGE_DIR / item.storage_key).unlink(missing_ok=True)
    if item.replacement_storage_key:
        (STORAGE_DIR / item.replacement_storage_key).unlink(missing_ok=True)
    db.delete(item)
    db.commit()


def delete_knowledge(db: Session, item: KnowledgeBase) -> None:
    if db.query(KnowledgeDocument.id).filter(
        KnowledgeDocument.knowledge_id == item.id,
        KnowledgeDocument.status == "processing",
    ).first():
        raise HTTPException(status_code=409, detail="知识库有文件正在处理，请稍后重试删除")
    item.status = "deleting"
    db.commit()
    for document in db.query(KnowledgeDocument).filter(KnowledgeDocument.knowledge_id == item.id).all():
        delete_document(db, document)
    db.delete(item)
    db.commit()
