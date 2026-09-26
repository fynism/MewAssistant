from fastapi import APIRouter

from backend.routers import auth, chat, sessions, knowledges, invitations, tool_debug, api_keys, platform, operations


router = APIRouter()
router.include_router(auth.router)
router.include_router(sessions.router)
router.include_router(chat.router)
router.include_router(knowledges.router)
router.include_router(invitations.router)
router.include_router(tool_debug.router)
router.include_router(api_keys.router)
router.include_router(platform.router)
router.include_router(operations.router)
