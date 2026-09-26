"""JWT-only management of personal MCP credentials."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.auth import get_current_user, get_db
from backend.models import PersonalApiKey, User
from backend.services.api_keys import create_key, metadata

router = APIRouter(prefix="/account/api-keys")


class KeyInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)


@router.post("", status_code=201)
def create_api_key(data: KeyInput, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    name = data.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="名称不能为空")
    item, token = create_key(db, user.id, name)
    return {**metadata(item), "key": token}


@router.get("")
def list_api_keys(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    items = db.query(PersonalApiKey).filter(PersonalApiKey.owner_id == user.id).order_by(
        PersonalApiKey.created_at.desc(), PersonalApiKey.id.desc()).all()
    return {"items": [metadata(item) for item in items]}


@router.delete("/{key_id}")
def revoke_api_key(key_id: str, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    item = db.query(PersonalApiKey).filter(PersonalApiKey.id == key_id,
                                          PersonalApiKey.owner_id == user.id).first()
    if item is None:
        raise HTTPException(status_code=404, detail="凭证不存在")
    if item.revoked_at is None:
        item.revoked_at = datetime.utcnow()
        db.commit()
    return metadata(item)
