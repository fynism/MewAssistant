"""JWT protected in-site previews of the read-only knowledge tools."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from backend.auth import get_current_user, get_db
from backend.models import User
from backend.services.knowledge_tools import list_knowledges, retrieve
from backend.services.rate_limits import enforce_rate_limits
from backend.core.config import settings


router = APIRouter(prefix="/tools/debug")


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


@router.post("/listKnowledges")
def debug_list_knowledges(data: ListKnowledgesInput,
                          user: User = Depends(get_current_user),
                          db: Session = Depends(get_db)):
    return list_knowledges(db, user.id, limit=data.limit, cursor=data.cursor)


@router.post("/retrieve")
def debug_retrieve(data: RetrieveInput,
                   user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    enforce_rate_limits((f"retrieve:user:{user.id}", settings.retrieval_rate_per_minute))
    return retrieve(db, user.id, query=data.query,
                    knowledge_ids=data.knowledge_ids, top_k=data.top_k)
