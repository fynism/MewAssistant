"""Validate uploads, enforce quotas and queue document replacements."""

import hashlib
import ntpath
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.documents.cleaning import ProcessingError, clean_text, decode_text
from backend.infrastructure import storage
from backend.infrastructure.database.models import KnowledgeBase, KnowledgeDocument, User
from .access import get_owned_knowledge

ALLOWED_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".txt"}


def _verify_file(data: bytes, suffix: str) -> None:
    if not data or len(data) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="文件为空或超过上传大小限制")
    if suffix == ".txt":
        try:
            if not clean_text(decode_text(data)).strip():
                raise ProcessingError("TXT 文件没有可检索文本")
        except ProcessingError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if suffix == ".pdf" and not data.startswith(b"%PDF-"):
        raise HTTPException(status_code=400, detail="PDF 文件格式无效")
    if suffix in {".docx", ".xlsx"} and not data.startswith(b"PK\x03\x04"):
        raise HTTPException(status_code=400, detail="Office 文件格式无效")
    if suffix in {".doc", ".xls"} and not data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        raise HTTPException(status_code=400, detail="Office 文件格式无效")


def _check_user_capacity(db: Session, owner_id: int, incoming_bytes: int,
                         replacing: KnowledgeDocument | None = None) -> None:
    # Lock the owner row so uploads to different knowledge bases share one quota decision.
    db.query(User.id).filter(User.id == owner_id).with_for_update().one()
    rows = db.query(KnowledgeDocument).join(KnowledgeBase).filter(
        KnowledgeBase.owner_id == owner_id).all()
    if replacing is None and len(rows) >= settings.max_user_documents:
        raise HTTPException(status_code=413, detail="已达到个人文件数量上限")
    def occupied_bytes(row: KnowledgeDocument) -> int:
        # M1/M2 uploads predate the size column; count their real stored bytes.
        path = storage.KNOWLEDGE_DOCUMENTS_DIR / row.storage_key
        original = row.size_bytes or (path.stat().st_size if path.exists() else 0)
        replacement = row.replacement_size_bytes or 0
        return original + replacement

    occupied = sum(occupied_bytes(row) for row in rows)
    if replacing is not None:
        occupied -= occupied_bytes(replacing)
        path = storage.KNOWLEDGE_DOCUMENTS_DIR / replacing.storage_key
        original = replacing.size_bytes or (path.stat().st_size if path.exists() else 0)
        occupied += original + incoming_bytes
    else:
        occupied += incoming_bytes
    if occupied > settings.max_user_storage_bytes:
        raise HTTPException(status_code=413, detail="已达到个人存储空间上限")


def _check_duplicate(db: Session, knowledge_id: str, digest: str, excluding: str | None = None):
    query = db.query(KnowledgeDocument).filter(KnowledgeDocument.knowledge_id == knowledge_id,
        KnowledgeDocument.content_hash == digest, KnowledgeDocument.status != "deleting")
    if excluding:
        query = query.filter(KnowledgeDocument.id != excluding)
    if query.first():
        raise HTTPException(status_code=409, detail="此知识库已上传相同内容的文件")


async def save_upload(db: Session, owner_id: int, knowledge_id: str, file: UploadFile) -> KnowledgeDocument:
    get_owned_knowledge(db, owner_id, knowledge_id)
    filename, suffix, data = await _read_upload(file)
    _check_user_capacity(db, owner_id, len(data))
    digest = hashlib.sha256(data).hexdigest()
    _check_duplicate(db, knowledge_id, digest)
    doc_id = str(uuid4())
    storage_key = doc_id + suffix
    storage.KNOWLEDGE_DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
    path = storage.KNOWLEDGE_DOCUMENTS_DIR / storage_key
    path.write_bytes(data)
    item = KnowledgeDocument(id=doc_id, knowledge_id=knowledge_id,
        filename=filename, storage_key=storage_key, file_type=suffix,
        size_bytes=len(data), content_hash=digest, status="pending")
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
        raise HTTPException(status_code=400, detail="仅支持 PDF、Word、Excel、TXT 文档")
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
    _check_user_capacity(db, owner_id, len(data), replacing=item)
    _check_duplicate(db, knowledge_id, hashlib.sha256(data).hexdigest(), excluding=item.id)
    storage_key = str(uuid4()) + suffix
    storage.KNOWLEDGE_DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
    path = storage.KNOWLEDGE_DOCUMENTS_DIR / storage_key
    path.write_bytes(data)
    try:
        item.replacement_storage_key = storage_key
        item.replacement_filename = filename
        item.replacement_file_type = suffix
        item.replacement_size_bytes = len(data)
        item.replacement_old_ready = item.status == "ready"
        item.content_hash = hashlib.sha256(data).hexdigest()
        item.status = "replacing"
        item.error_summary = None
        item.updated_at = datetime.utcnow()
        db.commit()
    except Exception:
        db.rollback()
        path.unlink(missing_ok=True)
        raise
    return item
