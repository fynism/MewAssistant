from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.infrastructure.database.session import Base


class CallAudit(Base):
    __tablename__ = "call_audits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True)
    key_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    operation: Mapped[str] = mapped_column(String(60), nullable=False)
    result_category: Mapped[str] = mapped_column(String(30), nullable=False)
    request_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True, nullable=False)
