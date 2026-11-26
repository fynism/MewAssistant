from typing import Any, Dict

from backend.core.config import settings
from backend.core.langfuse_tracing import trace_observation, update_observation
from backend.infrastructure import resources
from backend.infrastructure.database.session import SessionLocal
from backend.services.knowledge.access import visible_document_ids
from backend.infrastructure.database.models import KnowledgeDocument
from backend.documents.cleaning import public_report
from backend.rag.merging import AUTO_MERGE_ENABLED, AUTO_MERGE_THRESHOLD, auto_merge_documents
from backend.rag.rerank import RERANK_API_KEY, RERANK_BINDING_HOST, RERANK_MODEL, get_rerank_endpoint, rerank_documents


LEAF_RETRIEVE_LEVEL = settings.leaf_retrieve_level


def retrieve_documents(query: str, owner_id: int, top_k: int = 5, knowledge_ids: list[str] | None = None) -> Dict[str, Any]:
    with trace_observation("rag.retrieve", as_type="retriever", metadata={"top_k": top_k}) as observation:
        response = _retrieve_documents(query, owner_id, top_k, knowledge_ids)
        meta = response["meta"]
        failed = meta["retrieval_mode"] == "failed"
        update_observation(observation, metadata={
            "retrieval_mode": meta["retrieval_mode"],
            "candidate_count": meta.get("candidate_count", 0),
            "result_count": len(response["docs"]),
            "rerank_enabled": meta.get("rerank_enabled", False),
            "rerank_applied": meta.get("rerank_applied", False),
            "rerank_succeeded": bool(meta.get("rerank_applied") and not meta.get("rerank_error")),
        }, level="ERROR" if failed else None, status_message="retrieval_failed" if failed else None)
        return response


def _retrieve_documents(query: str, owner_id: int, top_k: int, knowledge_ids: list[str] | None) -> Dict[str, Any]:
    if not owner_id:
        raise ValueError("受限检索必须提供已认证用户")
    db = SessionLocal()
    try:
        document_ids = visible_document_ids(db, owner_id, knowledge_ids)
        reports = {row.id: public_report(row.processing_report) for row in db.query(KnowledgeDocument).filter(
            KnowledgeDocument.id.in_(document_ids)).all() if row.processing_report}
    finally:
        db.close()
    if not document_ids:
        return {"docs": [], "meta": {"retrieval_mode": "empty_scope", "candidate_count": 0}}
    allowed_document_ids = set(document_ids)
    candidate_k = max(top_k * 3, top_k)
    # All IDs are server-minted UUIDs, never raw user-supplied expression text.
    quoted_ids = ", ".join(f'"{item}"' for item in document_ids)
    filter_expr = f"chunk_level == {LEAF_RETRIEVE_LEVEL} and document_id in [{quoted_ids}]"
    for mode in ("hybrid", "dense_fallback"):
        try:
            with trace_observation("rag.embed", as_type="embedding", metadata={"retrieval_mode": mode}):
                dense_embedding = resources.embedding_service.get_embeddings([query])[0]
                if mode == "hybrid":
                    sparse_embedding = resources.embedding_service.get_sparse_embedding(query)

            with trace_observation("rag.search", as_type="retriever", metadata={
                "retrieval_mode": mode,
                "candidate_k": candidate_k,
            }) as observation:
                if mode == "hybrid":
                    retrieved = resources.milvus_manager.hybrid_retrieve(
                        dense_embedding=dense_embedding, sparse_embedding=sparse_embedding,
                        top_k=candidate_k, filter_expr=filter_expr,
                    )
                else:
                    retrieved = resources.milvus_manager.dense_retrieve(
                        dense_embedding=dense_embedding, top_k=candidate_k, filter_expr=filter_expr,
                    )
                retrieved = [item for item in retrieved if item.get("document_id") in allowed_document_ids]
                update_observation(observation, metadata={"candidate_count": len(retrieved)})

            with trace_observation("rag.rerank", metadata={"candidate_count": len(retrieved)}) as observation:
                reranked, rerank_meta = rerank_documents(query=query, docs=retrieved, top_k=top_k)
                rerank_failed = bool(rerank_meta.get("rerank_error"))
                update_observation(observation, metadata={
                    "rerank_enabled": rerank_meta.get("rerank_enabled", False),
                    "rerank_applied": rerank_meta.get("rerank_applied", False),
                    "rerank_succeeded": rerank_meta.get("rerank_applied", False) and not rerank_failed,
                }, level="WARNING" if rerank_failed else None,
                    status_message="rerank_fallback" if rerank_failed else None)

            with trace_observation("rag.merge") as observation:
                merged_docs, merge_meta = auto_merge_documents(
                    docs=reranked, top_k=top_k, allowed_document_ids=allowed_document_ids,
                )
                update_observation(observation, metadata={
                    "result_count": len(merged_docs),
                    "auto_merge_applied": merge_meta.get("auto_merge_applied", False),
                })

            rerank_meta["retrieval_mode"] = mode
            rerank_meta["candidate_k"] = candidate_k
            rerank_meta["leaf_retrieve_level"] = LEAF_RETRIEVE_LEVEL
            rerank_meta.update(merge_meta)
            for doc in merged_docs:
                if doc.get("document_id") in reports:
                    doc["processing_report"] = reports[doc["document_id"]]
            return {"docs": merged_docs, "meta": rerank_meta}
        except Exception:
            continue

    return {
        "docs": [],
        "meta": {
            "rerank_enabled": bool(RERANK_MODEL and RERANK_API_KEY and RERANK_BINDING_HOST),
            "rerank_applied": False,
            "rerank_model": RERANK_MODEL,
            "rerank_endpoint": get_rerank_endpoint(),
            "rerank_error": "retrieve_failed",
            "retrieval_mode": "failed",
            "candidate_k": candidate_k,
            "leaf_retrieve_level": LEAF_RETRIEVE_LEVEL,
            "auto_merge_enabled": AUTO_MERGE_ENABLED,
            "auto_merge_applied": False,
            "auto_merge_threshold": AUTO_MERGE_THRESHOLD,
            "auto_merge_replaced_chunks": 0,
            "auto_merge_steps": 0,
            "candidate_count": 0,
        },
    }
