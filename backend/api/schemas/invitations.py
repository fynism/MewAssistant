from datetime import datetime

from pydantic import BaseModel, Field


class CreateInvitation(BaseModel):
    max_uses: int = Field(default=1, ge=1, le=100)
    expires_at: datetime | None = None
