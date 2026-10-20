"""Private knowledge use cases shared by HTTP, MCP and background workers."""

from .access import get_owned_knowledge, get_owned_document, visible_document_ids, create_knowledge
from .uploads import ALLOWED_EXTENSIONS, save_upload, replace_document
from .processing import process_document, recover_pending_documents, reset_interrupted_documents
from .lifecycle import queue_reprocessing, delete_document, delete_knowledge

__all__ = [
    "ALLOWED_EXTENSIONS",
    "create_knowledge",
    "get_owned_knowledge",
    "get_owned_document",
    "visible_document_ids",
    "save_upload",
    "replace_document",
    "process_document",
    "recover_pending_documents",
    "reset_interrupted_documents",
    "queue_reprocessing",
    "delete_document",
    "delete_knowledge",
]
