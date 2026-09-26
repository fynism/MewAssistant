from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.auth import get_current_user, get_db
from backend.models import KnowledgeBase, KnowledgeDocument, User
from backend.services.knowledge_service import (
    create_knowledge, delete_document, delete_knowledge, get_owned_document,
    get_owned_knowledge, process_document, save_upload,
)

router = APIRouter()


class KnowledgeInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""


def knowledge_payload(item: KnowledgeBase) -> dict:
    return {"id": item.id, "name": item.name, "description": item.description,
            "status": item.status, "created_at": item.created_at}


def document_payload(item: KnowledgeDocument) -> dict:
    return {"id": item.id, "knowledge_id": item.knowledge_id, "filename": item.filename,
            "file_type": item.file_type, "status": item.status,
            "error_summary": item.error_summary, "created_at": item.created_at}


@router.post("/knowledges", status_code=201)
def create_knowledge_endpoint(data: KnowledgeInput, user: User = Depends(get_current_user),
                              db: Session = Depends(get_db)):
    return knowledge_payload(create_knowledge(db, user.id, data.name, data.description))


@router.get("/knowledges")
def list_knowledges(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    items = db.query(KnowledgeBase).filter(KnowledgeBase.owner_id == user.id,
        KnowledgeBase.status == "active").order_by(KnowledgeBase.created_at.desc()).all()
    return {"knowledges": [knowledge_payload(i) for i in items]}


@router.get("/knowledges/{knowledge_id}")
def get_knowledge(knowledge_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return knowledge_payload(get_owned_knowledge(db, user.id, knowledge_id))


@router.patch("/knowledges/{knowledge_id}")
def update_knowledge(knowledge_id: str, data: KnowledgeInput, user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    item = get_owned_knowledge(db, user.id, knowledge_id)
    item.name = data.name.strip()
    item.description = data.description.strip()
    item.updated_at = datetime.utcnow()
    db.commit()
    return knowledge_payload(item)


@router.delete("/knowledges/{knowledge_id}")
def remove_knowledge(knowledge_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    delete_knowledge(db, get_owned_knowledge(db, user.id, knowledge_id, include_deleting=True))
    return {"deleted": True}


@router.post("/knowledges/{knowledge_id}/documents", status_code=202)
async def upload_document(knowledge_id: str, tasks: BackgroundTasks, file: UploadFile = File(...),
                          user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = await save_upload(db, user.id, knowledge_id, file)
    tasks.add_task(process_document, item.id)
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


@router.post("/knowledges/{knowledge_id}/documents/{document_id}/retry", status_code=202)
def retry_document(knowledge_id: str, document_id: str, tasks: BackgroundTasks,
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = get_owned_document(db, user.id, knowledge_id, document_id)
    if item.status != "failed":
        raise HTTPException(status_code=409, detail="只有失败的文件可以重试")
    item.status = "pending"
    db.commit()
    tasks.add_task(process_document, item.id)
    return document_payload(item)


@router.delete("/knowledges/{knowledge_id}/documents/{document_id}")
def remove_document(knowledge_id: str, document_id: str, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    delete_document(db, get_owned_document(db, user.id, knowledge_id, document_id, include_deleting=True))
    return {"deleted": True}
