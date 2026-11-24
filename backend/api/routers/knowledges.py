from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.api.schemas.knowledge import KnowledgeInput, PreviewInput
from backend.api.schemas.tools import DocumentSearchInput
from backend.api.dependencies import get_current_user, get_db
from backend.core.config import settings
from backend.documents.cleaning import ProcessingError
from backend.documents.spreadsheet import (MAX_COLUMN, MAX_INDEX_CELLS, MAX_ROW, SPREADSHEET_SUFFIXES,
                                           SpreadsheetPreviewBusy, UnknownSheet, preview_spreadsheet)
from backend.infrastructure import storage
from backend.infrastructure.database.models import KnowledgeBase, KnowledgeDocument, KnowledgeDocumentPage, User
from backend.services.knowledge import (
    create_knowledge, delete_document, delete_knowledge, get_owned_document,
    get_owned_knowledge, replace_document, save_upload,
    ALLOWED_EXTENSIONS, queue_reprocessing,
)
from backend.services.platform.rate_limits import enforce_rate_limits
from backend.services.knowledge.tools import retrieve

router = APIRouter()


def knowledge_payload(item: KnowledgeBase, ready_document_count: int = 0) -> dict:
    return {"id": item.id, "name": item.name, "description": item.description,
            "status": item.status, "created_at": item.created_at,
            "updated_at": item.updated_at, "ready_document_count": ready_document_count,
            "has_ready_documents": ready_document_count > 0}


def document_payload(item: KnowledgeDocument) -> dict:
    return {"id": item.id, "knowledge_id": item.knowledge_id, "filename": item.filename,
            "file_type": item.file_type, "status": item.status,
            "error_summary": item.error_summary, "created_at": item.created_at,
            "processing_report": item.processing_report,
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


@router.get("/documents")
def list_all_documents(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    items = db.query(KnowledgeDocument).join(KnowledgeBase).filter(
        KnowledgeBase.owner_id == user.id, KnowledgeBase.status == "active",
        KnowledgeDocument.status != "deleting",
    ).order_by(KnowledgeDocument.created_at.desc()).all()
    return {"documents": [document_payload(i) for i in items]}


@router.post("/documents/search")
def search_documents(data: DocumentSearchInput, user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    enforce_rate_limits((f"retrieve:user:{user.id}", settings.retrieval_rate_per_minute))
    scope = [data.knowledge_id] if data.knowledge_id is not None else None
    return retrieve(db, user.id, data.query, scope, data.top_k)


@router.get("/knowledges/{knowledge_id}/documents/{document_id}")
def get_document(knowledge_id: str, document_id: str, user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    return document_payload(get_owned_document(db, user.id, knowledge_id, document_id))


def original_path(item: KnowledgeDocument) -> Path:
    root = storage.KNOWLEDGE_DOCUMENTS_DIR.resolve()
    path = (root / item.storage_key).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(status_code=404, detail="原文件不存在")
    return path


@router.get("/knowledges/{knowledge_id}/documents/{document_id}/content")
def get_document_content(knowledge_id: str, document_id: str, user: User = Depends(get_current_user),
                         db: Session = Depends(get_db)):
    item = get_owned_document(db, user.id, knowledge_id, document_id)
    path = original_path(item)
    return FileResponse(path, filename=item.filename,
                        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@router.post("/knowledges/{knowledge_id}/documents/{document_id}/preview/{page_number}")
def preview_document_page(knowledge_id: str, document_id: str, page_number: int, data: PreviewInput,
                          user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from pypdfium2 import PdfiumError
    from backend.documents.pdf import render_preview
    from backend.documents.word import WORD_SUFFIXES, WordConversionBusy, word_pdf

    item = get_owned_document(db, user.id, knowledge_id, document_id)
    path = original_path(item)
    suffix = path.suffix.lower()
    if suffix != ".pdf" and suffix not in WORD_SUFFIXES:
        raise HTTPException(status_code=415, detail="此格式不支持页面预览")
    try:
        if suffix in WORD_SUFFIXES:
            path = word_pdf(path, wait=False)
        image, count = render_preview(str(path), page_number, data.query.replace("\x00", "").strip())
    except WordConversionBusy as exc:
        raise HTTPException(status_code=503, detail=str(exc), headers={"Retry-After": "1"}) from exc
    except IndexError as exc:
        raise HTTPException(status_code=404, detail="页面不存在") from exc
    except (PdfiumError, ProcessingError, OSError) as exc:
        detail = "无法预览此 PDF" if suffix == ".pdf" else "无法预览此 Word 文件"
        if suffix in WORD_SUFFIXES and isinstance(exc, ProcessingError):
            detail = str(exc)
        raise HTTPException(status_code=422, detail=detail) from exc
    return Response(image, media_type="image/png",
                    headers={"X-PDF-Page-Count": str(count), "Cache-Control": "private, no-store"})


@router.get("/knowledges/{knowledge_id}/documents/{document_id}/spreadsheet-preview")
def preview_document_spreadsheet(
    knowledge_id: str, document_id: str,
    sheet: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    row: Annotated[int | None, Query(ge=1, le=MAX_ROW)] = None,
    column: Annotated[int | None, Query(ge=1, le=MAX_COLUMN)] = None,
    citation_page: Annotated[int | None, Query(ge=1, le=MAX_INDEX_CELLS)] = None,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    item = get_owned_document(db, user.id, knowledge_id, document_id)
    path = original_path(item)
    if path.suffix.lower() not in SPREADSHEET_SUFFIXES:
        raise HTTPException(status_code=415, detail="此格式不支持表格预览")
    location = {}
    if citation_page is not None and sheet is None:
        page = db.get(KnowledgeDocumentPage, (document_id, citation_page - 1))
        if page is not None:
            location = page.extraction_data.get("spreadsheet", {})
    try:
        payload = preview_spreadsheet(path, sheet or location.get("sheet"),
                                      row or location.get("row", 1), column or location.get("column", 1))
    except UnknownSheet as exc:
        raise HTTPException(status_code=404, detail="工作表不存在") from exc
    except SpreadsheetPreviewBusy as exc:
        raise HTTPException(status_code=503, detail=str(exc), headers={"Retry-After": "1"}) from exc
    except ProcessingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return JSONResponse({**payload, "citation_range": location.get("range")},
                        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


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
    enforce_rate_limits((f"upload:user:{user.id}", settings.upload_rate_per_minute))
    item = queue_reprocessing(db, user.id, knowledge_id, document_id)
    return document_payload(item)


@router.post("/knowledges/{knowledge_id}/documents/{document_id}/reprocess", status_code=202)
def reprocess_document(knowledge_id: str, document_id: str,
                       user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    enforce_rate_limits((f"upload:user:{user.id}", settings.upload_rate_per_minute))
    return document_payload(queue_reprocessing(db, user.id, knowledge_id, document_id))


@router.get("/knowledges/{knowledge_id}/documents/{document_id}/pages/{page_number}")
def page_details(knowledge_id: str, document_id: str, page_number: int,
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    get_owned_document(db, user.id, knowledge_id, document_id)
    page = db.get(KnowledgeDocumentPage, (document_id, page_number - 1))
    if page is None:
        raise HTTPException(status_code=404, detail="页面记录不存在，请先重新处理文件")
    return {**page.extraction_data, "page_number": page_number}


@router.delete("/knowledges/{knowledge_id}/documents/{document_id}")
def remove_document(knowledge_id: str, document_id: str, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    delete_document(db, get_owned_document(db, user.id, knowledge_id, document_id, include_deleting=True))
    return {"deleted": True}
