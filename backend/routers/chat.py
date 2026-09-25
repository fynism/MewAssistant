import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from backend.agent import chat_with_agent, chat_with_agent_stream
from backend.auth import get_current_user
from backend.models import User
from backend.schemas import ChatRequest, ChatResponse


router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest, current_user: User = Depends(get_current_user)):
    try:
        session_id = request.session_id or "default_session"
        resp = chat_with_agent(request.message, current_user.username, current_user.id, session_id)
        if isinstance(resp, dict):
            return ChatResponse(**resp)
        return ChatResponse(response=resp)
    except Exception:
        raise HTTPException(status_code=502, detail="对话服务暂时不可用")


@router.post("/chat/stream")
async def chat_stream_endpoint(request: ChatRequest, current_user: User = Depends(get_current_user)):
    """跟 Agent 对话 (流式)"""

    async def event_generator():
        try:
            session_id = request.session_id or "default_session"
            async for chunk in chat_with_agent_stream(request.message, current_user.username, current_user.id, session_id):
                yield chunk
        except Exception:
            error_data = {"type": "error", "content": "对话服务暂时不可用"}
            yield f"data: {json.dumps(error_data)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
