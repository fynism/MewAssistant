from typing import Any, Dict

from backend.core.config import settings
from backend.dependencies import embedding_service as _embedding_service, milvus_manager as _milvus_manager
from backend.database import SessionLocal
from backend.services.knowledge_service import visible_document_ids
from backend.rag.merging import AUTO_MERGE_ENABLED, AUTO_MERGE_THRESHOLD, auto_merge_documents
from backend.rag.rerank import RERANK_API_KEY, RERANK_BINDING_HOST, RERANK_MODEL, get_rerank_endpoint, rerank_documents


LEAF_RETRIEVE_LEVEL = settings.leaf_retrieve_level


def retrieve_documents(query: str, owner_id: int, top_k: int = 5, knowledge_ids: list[str] | None = None) -> Dict[str, Any]:
    if not owner_id:
        raise ValueError("受限检索必须提供已认证用户")
    db = SessionLocal()
    try:
        document_ids = visible_document_ids(db, owner_id, knowledge_ids)
    finally:
        db.close()
    if not document_ids:
        return {"docs": [], "meta": {"retrieval_mode": "empty_scope", "candidate_count": 0}}
    allowed_document_ids = set(document_ids)
    candidate_k = max(top_k * 3, top_k)
    # All IDs are server-minted UUIDs, never raw user-supplied expression text.
    quoted_ids = ", ".join(f'"{item}"' for item in document_ids)
    filter_expr = f"chunk_level == {LEAF_RETRIEVE_LEVEL} and document_id in [{quoted_ids}]"
    try:
        dense_embeddings = _embedding_service.get_embeddings([query])
        dense_embedding = dense_embeddings[0]
        sparse_embedding = _embedding_service.get_sparse_embedding(query)

        retrieved = _milvus_manager.hybrid_retrieve(
            dense_embedding=dense_embedding,
            sparse_embedding=sparse_embedding,
            top_k=candidate_k,
            filter_expr=filter_expr,
        )
        retrieved = [item for item in retrieved if item.get("document_id") in allowed_document_ids]
        reranked, rerank_meta = rerank_documents(query=query, docs=retrieved, top_k=top_k)
        merged_docs, merge_meta = auto_merge_documents(docs=reranked, top_k=top_k, allowed_document_ids=allowed_document_ids)
        rerank_meta["retrieval_mode"] = "hybrid"
        rerank_meta["candidate_k"] = candidate_k
        rerank_meta["leaf_retrieve_level"] = LEAF_RETRIEVE_LEVEL
        rerank_meta.update(merge_meta)
        return {"docs": merged_docs, "meta": rerank_meta}
    except Exception:
        try:
            dense_embeddings = _embedding_service.get_embeddings([query])
            dense_embedding = dense_embeddings[0]
            retrieved = _milvus_manager.dense_retrieve(
                dense_embedding=dense_embedding,
                top_k=candidate_k,
                filter_expr=filter_expr,
            )
            retrieved = [item for item in retrieved if item.get("document_id") in allowed_document_ids]
            reranked, rerank_meta = rerank_documents(query=query, docs=retrieved, top_k=top_k)
            merged_docs, merge_meta = auto_merge_documents(docs=reranked, top_k=top_k, allowed_document_ids=allowed_document_ids)
            rerank_meta["retrieval_mode"] = "dense_fallback"
            rerank_meta["candidate_k"] = candidate_k
            rerank_meta["leaf_retrieve_level"] = LEAF_RETRIEVE_LEVEL
            rerank_meta.update(merge_meta)
            return {"docs": merged_docs, "meta": rerank_meta}
        except Exception:
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
