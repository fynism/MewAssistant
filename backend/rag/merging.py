from collections import defaultdict
from typing import Any, Dict, List, Tuple

from backend.core.config import settings
from backend.database import SessionLocal
from backend.models import KnowledgeParentChunk


AUTO_MERGE_ENABLED = settings.auto_merge_enabled
AUTO_MERGE_THRESHOLD = settings.auto_merge_threshold


def merge_to_parent_level(docs: List[dict], allowed_document_ids: set[str], threshold: int = 2) -> Tuple[List[dict], int]:
    groups: Dict[str, List[dict]] = defaultdict(list)
    for doc in docs:
        parent_id = (doc.get("parent_chunk_id") or "").strip()
        if parent_id:
            groups[parent_id].append(doc)

    merge_parent_ids = [parent_id for parent_id, children in groups.items() if len(children) >= threshold]
    if not merge_parent_ids:
        return docs, 0

    if not allowed_document_ids:
        return docs, 0
    db = SessionLocal()
    try:
        rows = db.query(KnowledgeParentChunk).filter(
            KnowledgeParentChunk.chunk_id.in_(merge_parent_ids),
            KnowledgeParentChunk.document_id.in_(allowed_document_ids)).all()
        parent_map = {r.chunk_id: {
            "chunk_id": r.chunk_id, "knowledge_id": r.knowledge_id,
            "document_id": r.document_id, "text": r.text, "filename": r.filename,
            "file_type": r.file_type, "page_number": r.page_number,
            "parent_chunk_id": r.parent_chunk_id, "root_chunk_id": r.root_chunk_id,
            "chunk_level": r.chunk_level, "chunk_idx": r.chunk_idx,
        } for r in rows}
    finally:
        db.close()

    merged_docs: List[dict] = []
    merged_count = 0
    for doc in docs:
        parent_id = (doc.get("parent_chunk_id") or "").strip()
        if (not parent_id or parent_id not in parent_map
                or parent_map[parent_id]["document_id"] != doc.get("document_id")
                or parent_map[parent_id]["knowledge_id"] != doc.get("knowledge_id")):
            merged_docs.append(doc)
            continue
        parent_doc = dict(parent_map[parent_id])
        score = doc.get("score")
        if score is not None:
            parent_doc["score"] = max(float(parent_doc.get("score", score)), float(score))
        parent_doc["merged_from_children"] = True
        parent_doc["merged_child_count"] = len(groups[parent_id])
        merged_docs.append(parent_doc)
        merged_count += 1

    deduped: List[dict] = []
    seen = set()
    for item in merged_docs:
        key = item.get("chunk_id") or (item.get("filename"), item.get("page_number"), item.get("text"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)

    return deduped, merged_count


def auto_merge_documents(docs: List[dict], top_k: int, allowed_document_ids: set[str]) -> Tuple[List[dict], Dict[str, Any]]:
    if not AUTO_MERGE_ENABLED or not docs:
        return docs[:top_k], {
            "auto_merge_enabled": AUTO_MERGE_ENABLED,
            "auto_merge_applied": False,
            "auto_merge_threshold": AUTO_MERGE_THRESHOLD,
            "auto_merge_replaced_chunks": 0,
            "auto_merge_steps": 0,
        }

    # 两段自动合并：L3->L2，再 L2->L1。
    merged_docs, merged_count_l3_l2 = merge_to_parent_level(docs, allowed_document_ids, threshold=AUTO_MERGE_THRESHOLD)
    merged_docs, merged_count_l2_l1 = merge_to_parent_level(merged_docs, allowed_document_ids, threshold=AUTO_MERGE_THRESHOLD)

    merged_docs.sort(key=lambda item: item.get("score", 0.0), reverse=True)
    merged_docs = merged_docs[:top_k]

    replaced_count = merged_count_l3_l2 + merged_count_l2_l1
    return merged_docs, {
        "auto_merge_enabled": AUTO_MERGE_ENABLED,
        "auto_merge_applied": replaced_count > 0,
        "auto_merge_threshold": AUTO_MERGE_THRESHOLD,
        "auto_merge_replaced_chunks": replaced_count,
        "auto_merge_steps": int(merged_count_l3_l2 > 0) + int(merged_count_l2_l1 > 0),
    }
