"""Explicit offline reprocessing, optionally upgrading the BM25 tokenizer.

Stop the application worker first. Run: python -m backend.reindex_documents --all
Add --upgrade-tokenizer to rebuild the entire sparse index with numeric tokens.
"""

import argparse


def reindex_all(upgrade_tokenizer=False) -> dict:
    from backend.database import SessionLocal
    from backend.dependencies import embedding_service, milvus_manager
    from backend.models import KnowledgeBase, KnowledgeDocument
    from backend.services.knowledge_service import _cleanup_vectors, process_document

    with SessionLocal() as db:
        docs = db.query(KnowledgeDocument).join(KnowledgeBase).filter(KnowledgeBase.status == "active").all()
        if any(doc.status in {"processing", "replacing", "deleting"} for doc in docs):
            raise RuntimeError("有处理中或删除中的文件，请等待任务结束并停止应用后重建")
        ids = [doc.id for doc in docs]
        for doc in docs:
            doc.status = "pending"
        db.commit()  # Hide every old document before changing token dimensions.
        if upgrade_tokenizer:
            for doc in docs:
                _cleanup_vectors(db, doc, remove_bm25=True)
            if milvus_manager.query_all(output_fields=["document_id"]):
                raise RuntimeError("索引仍有其他文档；完成其删除后再升级分词器，已排队文档可重新运行本命令")
            embedding_service.reset_for_rebuild()
    for doc_id in ids:
        process_document(doc_id)
    with SessionLocal() as db:
        states = [doc.status for doc in db.query(KnowledgeDocument).filter(KnowledgeDocument.id.in_(ids)).all()]
    return {"total": len(ids), "ready": states.count("ready"), "failed": states.count("failed")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", required=True)
    parser.add_argument("--upgrade-tokenizer", action="store_true")
    args = parser.parse_args()
    result = reindex_all(args.upgrade_tokenizer)
    print(result)
    if result["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
