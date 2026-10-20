from pydantic import BaseModel, ConfigDict, Field

from backend.core.config import settings


class ListKnowledgesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=50, ge=1, le=100)
    cursor: str | None = None


class RetrieveInput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    query: str = Field(min_length=1, max_length=500)
    knowledge_ids: list[str] = Field(min_length=1, max_length=20, alias="knowledgeIds")
    top_k: int = Field(default=min(5, settings.max_retrieval_results), ge=1,
                       le=settings.max_retrieval_results, alias="topK")
