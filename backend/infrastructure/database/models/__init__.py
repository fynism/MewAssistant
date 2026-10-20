"""Register all ORM mappings for application queries and Alembic metadata."""

from .identity import User, PersonalApiKey, Invitation
from .chat import ChatSession, ChatMessage
from .knowledge import KnowledgeBase, KnowledgeDocument, KnowledgeParentChunk, KnowledgeDocumentPage
from .operations import CallAudit
from .legacy import ParentChunk

__all__ = [
    "User",
    "PersonalApiKey",
    "Invitation",
    "ChatSession",
    "ChatMessage",
    "KnowledgeBase",
    "KnowledgeDocument",
    "KnowledgeParentChunk",
    "KnowledgeDocumentPage",
    "CallAudit",
    "ParentChunk",
]
