"""Shared application dependencies.

This module centralizes stateful infrastructure instances so routers,
services, and RAG retrieval use the same clients where behavior depends on
shared process state.
"""

from backend.document_loader import DocumentLoader
from backend.embedding import embedding_service
from backend.milvus_client import MilvusManager
from backend.milvus_writer import MilvusWriter
from backend.services.conversation_storage import conversation_storage


document_loader = DocumentLoader()
milvus_manager = MilvusManager()
milvus_writer = MilvusWriter(embedding_service=embedding_service, milvus_manager=milvus_manager)
