from pydantic import BaseModel
from typing import Optional, List

from .chat import RagTrace


class MessageInfo(BaseModel):
    type: str
    content: str
    timestamp: str
    rag_trace: Optional[RagTrace] = None
    knowledge_ids: Optional[List[str]] = None


class SessionMessagesResponse(BaseModel):
    messages: List[MessageInfo]
    last_knowledge_ids: Optional[List[str]] = None
    legacy_scope_unknown: bool = False


class SessionInfo(BaseModel):
    session_id: str
    updated_at: str
    message_count: int
    title: Optional[str] = None
    last_knowledge_ids: Optional[List[str]] = None
    legacy_scope_unknown: bool = False


class SessionListResponse(BaseModel):
    sessions: List[SessionInfo]


class SessionDeleteResponse(BaseModel):
    session_id: str
    message: str
