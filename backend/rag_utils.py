"""Compatibility exports for the RAG utility modules."""

from backend.rag.expansion import generate_hypothetical_document, step_back_expand
from backend.rag.retrieval import retrieve_documents


__all__ = [
    "generate_hypothetical_document",
    "retrieve_documents",
    "step_back_expand",
]
