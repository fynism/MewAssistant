from fastapi import APIRouter

from backend.routers import auth, chat, sessions, knowledges, invitations


router = APIRouter()
router.include_router(auth.router)
router.include_router(sessions.router)
router.include_router(chat.router)
router.include_router(knowledges.router)
router.include_router(invitations.router)
