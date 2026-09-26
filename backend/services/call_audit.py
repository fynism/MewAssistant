"""Metadata-only audit persistence and 90-day retention."""

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from backend.database import SessionLocal
from backend.models import CallAudit
from backend.observability import logger, request_id


def record_call(owner_id: int | None, key_id: str | None, operation: str,
                category: str, duration_ms: int) -> None:
    try:
        with SessionLocal() as db:
            db.add(CallAudit(owner_id=owner_id, key_id=key_id, operation=operation,
                             result_category=category, request_id=request_id.get(),
                             duration_ms=max(0, duration_ms)))
            db.commit()
    except Exception:
        logger.exception("audit_write_failed request_id=%s operation=%s", request_id.get(), operation)


def prune_expired_audits(days: int = 90) -> int:
    threshold = datetime.utcnow() - timedelta(days=days)
    with SessionLocal() as db:
        removed = db.query(CallAudit).filter(CallAudit.created_at < threshold).delete()
        db.commit()
        return removed
