"""Shared application dependencies.

This module centralizes stateful infrastructure instances so routers,
services, and RAG retrieval use the same clients where behavior depends on
shared process state.
"""

from backend.document_loader import DocumentLoader
from backend.embedding import embedding_service
from backend.milvus_client import MilvusManager
from backend.milvus_writer import MilvusWriter
from backend.parent_chunk_store import ParentChunkStore
from backend.services.document_service import DocumentService


document_loader = DocumentLoader()
parent_chunk_store = ParentChunkStore()
milvus_manager = MilvusManager()
milvus_writer = MilvusWriter(embedding_service=embedding_service, milvus_manager=milvus_manager)
document_service = DocumentService(
    loader=document_loader,
    parent_chunk_store=parent_chunk_store,
    milvus_manager=milvus_manager,
    milvus_writer=milvus_writer,
)
