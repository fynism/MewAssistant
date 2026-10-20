"""Knowledge creation, ownership checks and retrieval visibility."""

from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from backend.infrastructure.database.models import KnowledgeBase, KnowledgeDocument


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
