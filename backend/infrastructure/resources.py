"""Shared application dependencies.

This module centralizes stateful infrastructure instances so routers,
services, and RAG retrieval use the same clients where behavior depends on
shared process state.
"""

from backend.documents.loader import DocumentLoader
from backend.infrastructure.embedding import embedding_service
from backend.infrastructure.vector_store.client import MilvusManager
from backend.infrastructure.vector_store.writer import MilvusWriter


document_loader = DocumentLoader()
milvus_manager = MilvusManager()
milvus_writer = MilvusWriter(embedding_service=embedding_service, milvus_manager=milvus_manager)
