"""Filesystem locations shared by upload, indexing and cleanup workflows."""

from backend.core.config import BASE_DIR

KNOWLEDGE_DOCUMENTS_DIR = BASE_DIR / "data" / "knowledge_documents"
LEGACY_DOCUMENTS_DIR = BASE_DIR / "data" / "documents"
