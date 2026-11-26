"""Filesystem locations shared by upload, indexing and cleanup workflows."""

from backend.core.config import BASE_DIR

KNOWLEDGE_DOCUMENTS_DIR = BASE_DIR / "data" / "knowledge_documents"
LEGACY_DOCUMENTS_DIR = BASE_DIR / "data" / "documents"


def remove_knowledge_file(storage_key: str) -> None:
    from backend.documents.word import remove_word_pdf

    source = KNOWLEDGE_DOCUMENTS_DIR / storage_key
    source.unlink(missing_ok=True)
    remove_word_pdf(source)
