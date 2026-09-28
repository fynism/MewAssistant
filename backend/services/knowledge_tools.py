"""Authenticated read-only knowledge tools shared by web debug and future MCP transport."""

import base64
import hashlib

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.models import KnowledgeBase, KnowledgeDocument
from backend.core.config import settings
from backend.services.knowledge_service import visible_document_ids


def _decode_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        value = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("ascii")
        offset = int(value)
        if offset < 0 or str(offset) != value:
            raise ValueError
        return offset
    except (ValueError, UnicodeError, base64.binascii.Error):
        raise HTTPException(status_code=422, detail="分页游标无效")


def list_knowledges(db: Session, owner_id: int, limit: int = 50,
                    cursor: str | None = None) -> dict:
    if not owner_id:
        raise HTTPException(status_code=401, detail="请先登录")
    if not 1 <= limit <= 100:
        raise HTTPException(status_code=422, detail="limit 必须在 1 到 100 之间")
    offset = _decode_cursor(cursor)
    items = db.query(KnowledgeBase).filter(
        KnowledgeBase.owner_id == owner_id, KnowledgeBase.status == "active",
    ).order_by(KnowledgeBase.created_at.desc(), KnowledgeBase.id.desc()).offset(offset).limit(limit + 1).all()
    page = items[:limit]
    ids = [item.id for item in page]
    counts = dict(db.query(KnowledgeDocument.knowledge_id, func.count(KnowledgeDocument.id))
        .filter(KnowledgeDocument.knowledge_id.in_(ids), KnowledgeDocument.status == "ready")
        .group_by(KnowledgeDocument.knowledge_id).all()) if ids else {}
    next_cursor = (base64.urlsafe_b64encode(str(offset + limit).encode()).decode()
                   if len(items) > limit else None)
    return {"items": [{"id": item.id, "name": item.name,
                        "description": item.description,
                        "hasReadyDocuments": counts.get(item.id, 0) > 0,
                        "readyDocumentCount": counts.get(item.id, 0)} for item in page],
            "nextCursor": next_cursor}


def retrieve(db: Session, owner_id: int, query: str,
             knowledge_ids: list[str], top_k: int = 5) -> dict:
    if not owner_id:
        raise HTTPException(status_code=401, detail="请先登录")
    query = query.strip()
    if not query or len(query) > 500:
        raise HTTPException(status_code=422, detail="query 长度必须为 1 到 500 字")
    if not 1 <= top_k <= settings.max_retrieval_results:
        raise HTTPException(status_code=422, detail=f"topK 必须在 1 到 {settings.max_retrieval_results} 之间")
    if (not knowledge_ids or len(knowledge_ids) > 20
            or any(not item or item != item.strip() for item in knowledge_ids)
            or len(set(knowledge_ids)) != len(knowledge_ids)):
        raise HTTPException(status_code=422, detail="knowledgeIds 必须包含 1 到 20 个不重复的知识库 ID")
    document_ids = visible_document_ids(db, owner_id, knowledge_ids)
    if not document_ids:
        return {"status": "no_documents", "results": []}

    from backend.rag.retrieval import retrieve_documents
    try:
        response = retrieve_documents(query, owner_id=owner_id,
                                      knowledge_ids=knowledge_ids, top_k=top_k)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=503, detail="检索服务暂时不可用")
    if response.get("meta", {}).get("retrieval_mode") == "failed":
        raise HTTPException(status_code=503, detail="检索服务暂时不可用")
    allowed_ids = set(document_ids)
    docs = [item for item in response.get("docs", [])
            if item.get("document_id") in allowed_ids][:top_k]
    if not docs:
        return {"status": "no_match", "results": []}

    records = db.query(KnowledgeDocument.id, KnowledgeDocument.filename,
                       KnowledgeBase.id, KnowledgeBase.name).join(
        KnowledgeBase, KnowledgeDocument.knowledge_id == KnowledgeBase.id).filter(
        KnowledgeDocument.id.in_([item["document_id"] for item in docs]),
        KnowledgeDocument.status == "ready", KnowledgeBase.owner_id == owner_id,
        KnowledgeBase.status == "active",
    ).all()
    source = {document_id: (filename, knowledge_id, knowledge_name)
              for document_id, filename, knowledge_id, knowledge_name in records}
    results = []
    for item in docs:
        document_id = item["document_id"]
        if document_id not in source:
            continue
        filename, knowledge_id, knowledge_name = source[document_id]
        chunk_id = item.get("chunk_id") or hashlib.sha256(
            f"{item.get('page_number')}:{item.get('text', '')}".encode()).hexdigest()[:20]
        result = {"rank": len(results) + 1, "sourceId": f"{document_id}:{chunk_id}",
                  "knowledgeId": knowledge_id, "knowledgeName": knowledge_name,
                  "documentId": document_id, "filename": filename,
                  "pageNumber": item.get("page_number"), "text": item.get("text", "")}
        if item.get("score") is not None:
            result["retrievalScore"] = item["score"]
        if item.get("rerank_score") is not None:
            result["rerankScore"] = item["rerank_score"]
        results.append(result)
    return {"status": "ok" if results else "no_match", "results": results}
