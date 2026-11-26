from pydantic import BaseModel, ConfigDict, Field

from backend.core.config import settings


class ListKnowledgesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=50, ge=1, le=100)
    cursor: str | None = None


class RetrievalQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    query: str = Field(min_length=1, max_length=500)
    top_k: int = Field(default=min(5, settings.max_retrieval_results), ge=1,
                       le=settings.max_retrieval_results, alias="topK")


class RetrieveInput(RetrievalQuery):
    knowledge_ids: list[str] = Field(min_length=1, max_length=20, alias="knowledgeIds")


class DocumentSearchInput(RetrievalQuery):
    knowledge_id: str | None = Field(default=None, min_length=1, max_length=36, alias="knowledgeId")
