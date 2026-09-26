"""Administrator-only aggregate usage and failure view."""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.auth import get_db, require_admin
from backend.models import CallAudit, User

router = APIRouter()


@router.get("/admin/operations")
def operations(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    since = datetime.utcnow() - timedelta(hours=24)
    base = db.query(CallAudit).filter(CallAudit.created_at >= since)
    count = base.count()
    errors = base.filter(CallAudit.result_category != "success").count()
    average = db.query(func.avg(CallAudit.duration_ms)).filter(CallAudit.created_at >= since).scalar()
    by_operation = db.query(CallAudit.operation, func.count(CallAudit.id)).filter(
        CallAudit.created_at >= since).group_by(CallAudit.operation).all()
    failures = base.filter(CallAudit.result_category != "success").order_by(
        CallAudit.created_at.desc()).limit(20).all()
    return {
        "windowHours": 24, "calls": count, "errors": errors,
        "averageDurationMs": round(float(average or 0), 1),
        "byOperation": [{"name": name, "count": total} for name, total in by_operation],
        "recentFailures": [{"createdAt": item.created_at, "requestId": item.request_id,
                            "ownerId": item.owner_id, "keyId": item.key_id,
                            "operation": item.operation, "category": item.result_category}
                           for item in failures],
    }
