from fastapi import APIRouter, Depends, HTTPException

from backend.auth import get_current_user
from backend.models import User
from backend.schemas import (
    MessageInfo,
    SessionDeleteResponse,
    SessionInfo,
    SessionListResponse,
    SessionMessagesResponse,
)
from backend.services.conversation_storage import conversation_storage as storage


router = APIRouter()


@router.get("/sessions/{session_id}", response_model=SessionMessagesResponse)
async def get_session_messages(session_id: str, current_user: User = Depends(get_current_user)):
    """获取指定会话的所有消息"""
    try:
        messages = [
            MessageInfo(
                type=msg["type"],
                content=msg["content"],
                timestamp=msg["timestamp"],
                rag_trace=msg.get("rag_trace"),
                knowledge_ids=msg.get("knowledge_ids"),
            )
            for msg in storage.get_session_messages(current_user.username, session_id)
        ]
        metadata = storage.get_session_metadata(current_user.username, session_id)
        return SessionMessagesResponse(
            messages=messages,
            last_knowledge_ids=metadata.get("last_knowledge_ids"),
            legacy_scope_unknown="last_knowledge_ids" not in metadata,
        )
    except Exception:
        raise HTTPException(status_code=500, detail="会话读取失败")


@router.get("/sessions", response_model=SessionListResponse)
async def list_sessions(current_user: User = Depends(get_current_user)):
    """获取当前用户的所有会话列表"""
    try:
        sessions = [SessionInfo(**item) for item in storage.list_session_infos(current_user.username)]
        sessions.sort(key=lambda x: x.updated_at, reverse=True)
        return SessionListResponse(sessions=sessions)
    except Exception:
        raise HTTPException(status_code=500, detail="会话列表读取失败")


@router.delete("/sessions/{session_id}", response_model=SessionDeleteResponse)
async def delete_session(session_id: str, current_user: User = Depends(get_current_user)):
    """删除当前用户的指定会话"""
    try:
        deleted = storage.delete_session(current_user.username, session_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="会话不存在")
        return SessionDeleteResponse(session_id=session_id, message="成功删除会话")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="会话删除失败")
