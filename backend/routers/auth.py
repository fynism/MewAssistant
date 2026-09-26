from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime
import hashlib

from backend.auth import (
    authenticate_user,
    create_access_token,
    get_current_user,
    get_db,
    get_password_hash,
)
from backend.models import Invitation, User
from backend.schemas import AuthResponse, CurrentUserResponse, LoginRequest, RegisterRequest


router = APIRouter()


@router.post("/auth/register", response_model=AuthResponse)
async def register(request: RegisterRequest, db: Session = Depends(get_db)):
    username = (request.username or "").strip()
    password = (request.password or "").strip()
    if not username or not password:
        raise HTTPException(status_code=400, detail="用户名和密码不能为空")

    code_hash = hashlib.sha256(request.invite_code.strip().encode("utf-8")).hexdigest()
    invitation = db.query(Invitation).filter(Invitation.code_hash == code_hash).with_for_update().first()
    if (invitation is None or invitation.revoked_at is not None
            or (invitation.expires_at is not None and invitation.expires_at <= datetime.utcnow())
            or invitation.used_count >= invitation.max_uses):
        raise HTTPException(status_code=403, detail="邀请码无效或已失效")
    if db.query(User).filter(User.username == username).first():
        raise HTTPException(status_code=409, detail="用户名已存在")
    user = User(username=username, password_hash=get_password_hash(password), role="user")
    invitation.used_count += 1
    db.add(user)
    db.commit()

    token = create_access_token(username=username, role="user")
    return AuthResponse(access_token=token, username=username, role="user")


@router.post("/auth/login", response_model=AuthResponse)
async def login(request: LoginRequest, db: Session = Depends(get_db)):
    user = authenticate_user(db, request.username, request.password)
    if not user:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = create_access_token(username=user.username, role=user.role)
    return AuthResponse(access_token=token, username=user.username, role=user.role)


@router.get("/auth/me", response_model=CurrentUserResponse)
async def me(current_user: User = Depends(get_current_user)):
    return CurrentUserResponse(username=current_user.username, role=current_user.role)
