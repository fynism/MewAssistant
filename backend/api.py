from fastapi import APIRouter

from backend.routers import auth, chat, documents, sessions


router = APIRouter()
router.include_router(auth.router)
router.include_router(sessions.router)
router.include_router(chat.router)
router.include_router(documents.router)
