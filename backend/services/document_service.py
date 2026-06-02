import os
from pathlib import Path

from fastapi import HTTPException, UploadFile

from backend.document_loader import DocumentLoader
from backend.embedding import embedding_service
from backend.milvus_client import MilvusManager
from backend.milvus_writer import MilvusWriter
from backend.parent_chunk_store import ParentChunkStore
from backend.schemas import DocumentDeleteResponse, DocumentInfo, DocumentListResponse, DocumentUploadResponse


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR.parent / "data"
UPLOAD_DIR = DATA_DIR / "documents"


class DocumentService:
    def __init__(
        self,
        loader: DocumentLoader | None = None,
        parent_chunk_store: ParentChunkStore | None = None,
        milvus_manager: MilvusManager | None = None,
        milvus_writer: MilvusWriter | None = None,
    ) -> None:
        self.loader = loader or DocumentLoader()
        self.parent_chunk_store = parent_chunk_store or ParentChunkStore()
        self.milvus_manager = milvus_manager or MilvusManager()
        self.milvus_writer = milvus_writer or MilvusWriter(
            embedding_service=embedding_service,
            milvus_manager=self.milvus_manager,
        )

    def remove_bm25_stats_for_filename(self, filename: str) -> None:
        """删除 Milvus 中该文件对应 chunk 前，先从持久化 BM25 统计中扣减。"""
        rows = self.milvus_manager.query_all(
            filter_expr=f'filename == "{filename}"',
            output_fields=["text"],
        )
        texts = [r.get("text") or "" for r in rows]
        embedding_service.increment_remove_documents(texts)

    def list_documents(self) -> DocumentListResponse:
        try:
            self.milvus_manager.init_collection()

            results = self.milvus_manager.query(
                output_fields=["filename", "file_type"],
                limit=10000,
            )

            file_stats = {}
            for item in results:
                filename = item.get("filename", "")
                file_type = item.get("file_type", "")
                if filename not in file_stats:
                    file_stats[filename] = {
                        "filename": filename,
                        "file_type": file_type,
                        "chunk_count": 0,
                    }
                file_stats[filename]["chunk_count"] += 1

            documents = [DocumentInfo(**stats) for stats in file_stats.values()]
            return DocumentListResponse(documents=documents)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"获取文档列表失败: {str(e)}")

    async def upload_document(self, file: UploadFile) -> DocumentUploadResponse:
        try:
            filename = file.filename or ""
            file_lower = filename.lower()
            if not filename:
                raise HTTPException(status_code=400, detail="文件名不能为空")
            if not (
                file_lower.endswith(".pdf")
                or file_lower.endswith((".docx", ".doc"))
                or file_lower.endswith((".xlsx", ".xls"))
            ):
                raise HTTPException(status_code=400, detail="仅支持 PDF、Word 和 Excel 文档")

            os.makedirs(UPLOAD_DIR, exist_ok=True)
            self.milvus_manager.init_collection()

            delete_expr = f'filename == "{filename}"'
            try:
                self.remove_bm25_stats_for_filename(filename)
            except Exception:
                pass
            try:
                self.milvus_manager.delete(delete_expr)
            except Exception:
                pass
            try:
                self.parent_chunk_store.delete_by_filename(filename)
            except Exception:
                pass

            file_path = UPLOAD_DIR / filename
            with open(file_path, "wb") as f:
                content = await file.read()
                f.write(content)

            try:
                new_docs = self.loader.load_document(str(file_path), filename)
            except Exception as doc_err:
                raise HTTPException(status_code=500, detail=f"文档处理失败: {doc_err}")

            if not new_docs:
                raise HTTPException(status_code=500, detail="文档处理失败，未能提取内容")

            parent_docs = [doc for doc in new_docs if int(doc.get("chunk_level", 0) or 0) in (1, 2)]
            leaf_docs = [doc for doc in new_docs if int(doc.get("chunk_level", 0) or 0) == 3]
            if not leaf_docs:
                raise HTTPException(status_code=500, detail="文档处理失败，未生成可检索叶子分块")

            self.parent_chunk_store.upsert_documents(parent_docs)
            self.milvus_writer.write_documents(leaf_docs)

            return DocumentUploadResponse(
                filename=filename,
                chunks_processed=len(leaf_docs),
                message=(
                    f"成功上传并处理 {filename}，叶子分块 {len(leaf_docs)} 个，"
                    f"父级分块 {len(parent_docs)} 个（存入 PostgreSQL）"
                ),
            )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"文档上传失败: {str(e)}")

    def delete_document(self, filename: str) -> DocumentDeleteResponse:
        try:
            self.milvus_manager.init_collection()

            delete_expr = f'filename == "{filename}"'
            self.remove_bm25_stats_for_filename(filename)
            result = self.milvus_manager.delete(delete_expr)
            self.parent_chunk_store.delete_by_filename(filename)

            return DocumentDeleteResponse(
                filename=filename,
                chunks_deleted=result.get("delete_count", 0) if isinstance(result, dict) else 0,
                message=f"成功删除文档 {filename} 的向量数据（本地文件已保留）",
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"删除文档失败: {str(e)}")
