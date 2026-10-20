"""Reprocessing and scoped removal of private documents and knowledge bases."""

from datetime import datetime

from fastapi import HTTPException
from sqlalchemy.orm import Session

from backend.infrastructure import storage
from backend.infrastructure.database.models import KnowledgeBase, KnowledgeDocument, KnowledgeDocumentPage
from .access import get_owned_knowledge
from .processing import cleanup_document_index


def queue_reprocessing(db: Session, owner_id: int, knowledge_id: str, document_id: str) -> KnowledgeDocument:
    get_owned_knowledge(db, owner_id, knowledge_id)
    item = db.query(KnowledgeDocument).filter(KnowledgeDocument.id == document_id,
        KnowledgeDocument.knowledge_id == knowledge_id).with_for_update().first()
    if item is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    if item.status not in {"ready", "failed"}:
        raise HTTPException(status_code=409, detail="文件正在处理，请稍后重试")
    item.status = "pending"
    item.error_summary = None
    item.updated_at = datetime.utcnow()
    db.commit()
    return item


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
    cleanup_document_index(db, item)
    (storage.KNOWLEDGE_DOCUMENTS_DIR / item.storage_key).unlink(missing_ok=True)
    db.query(KnowledgeDocumentPage).filter(KnowledgeDocumentPage.document_id == item.id).delete()
    if item.replacement_storage_key:
        (storage.KNOWLEDGE_DOCUMENTS_DIR / item.replacement_storage_key).unlink(missing_ok=True)
    db.delete(item)
    db.commit()


def delete_knowledge(db: Session, item: KnowledgeBase) -> None:
    item = db.query(KnowledgeBase).filter(KnowledgeBase.id == item.id).with_for_update().first()
    if item is None or item.status not in {"active", "deleting"}:
        raise HTTPException(status_code=404, detail="知识库不存在")
    if item.status == "active":
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
