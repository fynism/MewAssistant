"""Credential verification against the user store."""

from sqlalchemy.orm import Session

from backend.core.security import verify_password
from backend.infrastructure.database.models import User


def authenticate_user(db: Session, username: str, password: str) -> User | None:
    user = db.query(User).filter(User.username == username).first()
    if not user:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user
