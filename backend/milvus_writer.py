"""Write only tenant-tagged leaf chunks to the M1 collection."""

from backend.embedding import EmbeddingService, embedding_service as _default_embedding_service
from backend.milvus_client import MilvusManager


class MilvusWriter:
    def __init__(self, embedding_service: EmbeddingService = None, milvus_manager: MilvusManager = None):
        self.embedding_service = embedding_service or _default_embedding_service
        self.milvus_manager = milvus_manager or MilvusManager()

    def write_documents(self, documents: list[dict], batch_size: int = 50):
        if not documents:
            return
        document_ids = {doc["document_id"] for doc in documents}
        if len(document_ids) != 1 or any(not doc.get("knowledge_id") for doc in documents):
            raise ValueError("一次写入必须只包含一个有归属的文档")
        document_id = next(iter(document_ids))
        texts = [doc["text"] for doc in documents]
        self.milvus_manager.init_collection()
        self.embedding_service.increment_add_documents(texts)
        try:
            for i in range(0, len(documents), batch_size):
                batch = documents[i:i + batch_size]
                dense, sparse = self.embedding_service.get_all_embeddings([doc["text"] for doc in batch])
                payload = [{
                    "dense_embedding": dense_vec, "sparse_embedding": sparse_vec,
                    "text": doc["text"], "filename": doc["filename"],
                    "knowledge_id": doc["knowledge_id"], "document_id": doc["document_id"],
                    "file_type": doc["file_type"], "file_path": doc.get("file_path", ""),
                    "page_number": doc.get("page_number", 0), "chunk_idx": doc.get("chunk_idx", 0),
                    "chunk_id": doc["chunk_id"], "parent_chunk_id": doc.get("parent_chunk_id", ""),
                    "root_chunk_id": doc.get("root_chunk_id", ""), "chunk_level": doc["chunk_level"],
                } for doc, dense_vec, sparse_vec in zip(batch, dense, sparse)]
                self.milvus_manager.insert(payload)
            self.milvus_manager.flush()
        except Exception:
            # Never leave a failed document searchable; the DB also keeps it
            # non-ready until the entire operation succeeds.
            try:
                self.milvus_manager.delete(f'document_id == "{document_id}"')
            finally:
                self.embedding_service.increment_remove_documents(texts)
            raise
