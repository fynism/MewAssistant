"""Personal API keys: only the high-entropy digest is persisted."""

import hashlib
import hmac
import secrets
from datetime import datetime
from uuid import uuid4

from sqlalchemy.orm import Session

from backend.models import PersonalApiKey


def metadata(item: PersonalApiKey) -> dict:
    return {"id": item.id, "name": item.name, "suffix": item.token_suffix,
            "createdAt": item.created_at, "lastUsedAt": item.last_used_at,
            "revokedAt": item.revoked_at}


def create_key(db: Session, owner_id: int, name: str) -> tuple[PersonalApiKey, str]:
    key_id = str(uuid4())
    secret = secrets.token_urlsafe(32)
    token = f"smk_{key_id}_{secret}"
    item = PersonalApiKey(id=key_id, owner_id=owner_id, name=name.strip(),
                          token_digest=hashlib.sha256(token.encode()).hexdigest(),
                          token_suffix=secret[-8:])
    db.add(item)
    db.commit()
    return item, token


def authenticate_key(db: Session, token: str) -> int | None:
    if not token.startswith("smk_") or len(token) > 160:
        return None
    try:
        key_id, secret = token[4:].split("_", 1)
        if not key_id or not secret:
            return None
    except ValueError:
        return None
    item = db.get(PersonalApiKey, key_id)
    if item is None or item.revoked_at is not None:
        return None
    digest = hashlib.sha256(token.encode()).hexdigest()
    if not hmac.compare_digest(item.token_digest, digest):
        return None
    item.last_used_at = datetime.utcnow()
    db.commit()
    return item.owner_id
