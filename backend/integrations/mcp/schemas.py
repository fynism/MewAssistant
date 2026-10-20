"""Structured results exposed by the knowledge MCP adapter."""

from pydantic import BaseModel


class KnowledgeItem(BaseModel):
    id: str
    name: str
    description: str
    hasReadyDocuments: bool
    readyDocumentCount: int

class KnowledgeList(BaseModel):
    items: list[KnowledgeItem]
    nextCursor: str | None

class RetrievalHit(BaseModel):
    rank: int
    sourceId: str
    knowledgeId: str
    knowledgeName: str
    documentId: str
    filename: str
    pageNumber: int | None = None
    text: str
    retrievalScore: float | None = None
    rerankScore: float | None = None
    processingReport: dict | None = None

class RetrievalResult(BaseModel):
    status: str
    results: list[RetrievalHit]
