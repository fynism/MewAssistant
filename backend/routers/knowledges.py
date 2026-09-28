from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.auth import get_current_user, get_db
from backend.core.config import settings
from backend.models import KnowledgeBase, KnowledgeDocument, User
from backend.services.knowledge_service import (
    create_knowledge, delete_document, delete_knowledge, get_owned_document,
    get_owned_knowledge, replace_document, save_upload,
    ALLOWED_EXTENSIONS,
)
from backend.services.rate_limits import enforce_rate_limits

router = APIRouter()


class KnowledgeInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""


def knowledge_payload(item: KnowledgeBase, ready_document_count: int = 0) -> dict:
    return {"id": item.id, "name": item.name, "description": item.description,
            "status": item.status, "created_at": item.created_at,
            "updated_at": item.updated_at, "ready_document_count": ready_document_count,
            "has_ready_documents": ready_document_count > 0}


def document_payload(item: KnowledgeDocument) -> dict:
    return {"id": item.id, "knowledge_id": item.knowledge_id, "filename": item.filename,
            "file_type": item.file_type, "status": item.status,
            "error_summary": item.error_summary, "created_at": item.created_at,
            "updated_at": item.updated_at}


def ready_count(db: Session, knowledge_id: str) -> int:
    return db.query(func.count(KnowledgeDocument.id)).filter(
        KnowledgeDocument.knowledge_id == knowledge_id,
        KnowledgeDocument.status == "ready",
    ).scalar() or 0


@router.get("/knowledge-settings")
def knowledge_settings():
    return {"max_upload_bytes": settings.max_upload_bytes,
            "max_user_documents": settings.max_user_documents,
            "max_user_storage_bytes": settings.max_user_storage_bytes,
            "max_retrieval_results": settings.max_retrieval_results,
            "allowed_extensions": sorted(ALLOWED_EXTENSIONS)}


@router.post("/knowledges", status_code=201)
def create_knowledge_endpoint(data: KnowledgeInput, user: User = Depends(get_current_user),
                              db: Session = Depends(get_db)):
    return knowledge_payload(create_knowledge(db, user.id, data.name, data.description))


@router.get("/knowledges")
def list_knowledges(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    items = db.query(KnowledgeBase).filter(KnowledgeBase.owner_id == user.id,
        KnowledgeBase.status == "active").order_by(KnowledgeBase.created_at.desc()).all()
    counts = dict(db.query(KnowledgeDocument.knowledge_id, func.count(KnowledgeDocument.id))
        .filter(KnowledgeDocument.knowledge_id.in_([item.id for item in items]),
                KnowledgeDocument.status == "ready")
        .group_by(KnowledgeDocument.knowledge_id).all()) if items else {}
    return {"knowledges": [knowledge_payload(i, counts.get(i.id, 0)) for i in items]}


@router.get("/knowledges/{knowledge_id}")
def get_knowledge(knowledge_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return knowledge_payload(get_owned_knowledge(db, user.id, knowledge_id), ready_count(db, knowledge_id))


@router.patch("/knowledges/{knowledge_id}")
def update_knowledge(knowledge_id: str, data: KnowledgeInput, user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    item = get_owned_knowledge(db, user.id, knowledge_id)
    item.name = data.name.strip()
    item.description = data.description.strip()
    item.updated_at = datetime.utcnow()
    db.commit()
    return knowledge_payload(item, ready_count(db, knowledge_id))


@router.delete("/knowledges/{knowledge_id}")
def remove_knowledge(knowledge_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    delete_knowledge(db, get_owned_knowledge(db, user.id, knowledge_id, include_deleting=True))
    return {"deleted": True}


@router.post("/knowledges/{knowledge_id}/documents", status_code=202)
async def upload_document(knowledge_id: str, file: UploadFile = File(...),
                          user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    enforce_rate_limits((f"upload:user:{user.id}", settings.upload_rate_per_minute))
    item = await save_upload(db, user.id, knowledge_id, file)
    return document_payload(item)


@router.get("/knowledges/{knowledge_id}/documents")
def list_documents(knowledge_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    get_owned_knowledge(db, user.id, knowledge_id)
    items = db.query(KnowledgeDocument).filter(KnowledgeDocument.knowledge_id == knowledge_id,
        KnowledgeDocument.status != "deleting").order_by(KnowledgeDocument.created_at.desc()).all()
    return {"documents": [document_payload(i) for i in items]}


@router.get("/knowledges/{knowledge_id}/documents/{document_id}")
def get_document(knowledge_id: str, document_id: str, user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    return document_payload(get_owned_document(db, user.id, knowledge_id, document_id))


@router.put("/knowledges/{knowledge_id}/documents/{document_id}/content", status_code=202)
async def replace_document_endpoint(knowledge_id: str, document_id: str,
                                    file: UploadFile = File(...), user: User = Depends(get_current_user),
                                    db: Session = Depends(get_db)):
    enforce_rate_limits((f"upload:user:{user.id}", settings.upload_rate_per_minute))
    item = await replace_document(db, user.id, knowledge_id, document_id, file)
    return document_payload(item)


@router.post("/knowledges/{knowledge_id}/documents/{document_id}/retry", status_code=202)
def retry_document(knowledge_id: str, document_id: str,
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = get_owned_document(db, user.id, knowledge_id, document_id)
    if item.status != "failed":
        raise HTTPException(status_code=409, detail="只有失败的文件可以重试")
    item.status = "pending"
    db.commit()
    return document_payload(item)


@router.delete("/knowledges/{knowledge_id}/documents/{document_id}")
def remove_document(knowledge_id: str, document_id: str, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    delete_document(db, get_owned_document(db, user.id, knowledge_id, document_id, include_deleting=True))
    return {"deleted": True}
