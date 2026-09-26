import hashlib
import secrets
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.auth import get_db, require_admin
from backend.models import Invitation, User

router = APIRouter()


class CreateInvitation(BaseModel):
    max_uses: int = Field(default=1, ge=1, le=100)
    expires_at: datetime | None = None


@router.post("/admin/invitations")
def create_invitation(data: CreateInvitation, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    expiry = data.expires_at
    if expiry is not None and expiry.tzinfo is not None:
        expiry = expiry.astimezone(timezone.utc).replace(tzinfo=None)
    if expiry is not None and expiry <= datetime.utcnow():
        raise HTTPException(status_code=400, detail="过期时间必须晚于当前时间")
    code = secrets.token_urlsafe(24)
    item = Invitation(id=str(uuid4()), code_hash=hashlib.sha256(code.encode()).hexdigest(),
                      created_by=admin.id, max_uses=data.max_uses, expires_at=expiry)
    db.add(item)
    db.commit()
    return {"id": item.id, "invite_code": code, "max_uses": item.max_uses,
            "expires_at": item.expires_at, "message": "请立即保存邀请码，之后无法再次查看"}


@router.get("/admin/invitations")
def list_invitations(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    items = db.query(Invitation).order_by(Invitation.created_at.desc()).all()
    return [{"id": i.id, "created_at": i.created_at, "max_uses": i.max_uses, "used_count": i.used_count,
             "expires_at": i.expires_at, "revoked_at": i.revoked_at} for i in items]


@router.post("/admin/invitations/{invitation_id}/revoke")
def revoke_invitation(invitation_id: str, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    item = db.query(Invitation).filter(Invitation.id == invitation_id).first()
    if item is None:
        raise HTTPException(status_code=404, detail="邀请码不存在")
    item.revoked_at = datetime.utcnow()
    db.commit()
    return {"id": item.id, "revoked": True}
