"""JWT protected in-site previews of the read-only knowledge tools."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.api.schemas.tools import ListKnowledgesInput, RetrieveInput
from backend.api.dependencies import get_current_user, get_db
from backend.infrastructure.database.models import User
from backend.services.knowledge.tools import list_knowledges, retrieve
from backend.services.platform.rate_limits import enforce_rate_limits
from backend.core.config import settings


router = APIRouter(prefix="/tools/debug")


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
