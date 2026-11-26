from contextlib import aclosing
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from backend.services.chat.agent import chat_with_agent, chat_with_agent_stream
from backend.api.dependencies import get_current_user, get_db
from backend.infrastructure.database.models import User
from backend.api.schemas.chat import ChatRequest, ChatResponse
from backend.core.langfuse_tracing import (
    TRACE_ID_HEADER, current_trace_root, trace_observation, update_observation,
)
from backend.services.knowledge.access import visible_document_ids


router = APIRouter()
logger = logging.getLogger("uvicorn.error")


class _ChatStreamingResponse(StreamingResponse):
    async def stream_response(self, send):
        # Start in the task that consumes the iterator so the trace ID is
        # available before the response headers and children inherit the span.
        with trace_observation("chat.stream", root=True) as observation:
            if observation is not None:
                self.headers[TRACE_ID_HEADER] = observation.trace_id
            async with aclosing(self.body_iterator):
                await super().stream_response(send)


@router.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest, response: Response, current_user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    scope = list(dict.fromkeys(request.knowledge_ids)) if request.knowledge_ids is not None else None
    visible_document_ids(db, current_user.id, scope)
    try:
        session_id = request.session_id or "default_session"
        with trace_observation("chat", root=True) as observation:
            resp = chat_with_agent(request.message, current_user.username, current_user.id,
                                   session_id, knowledge_ids=scope)
        if observation is not None:
            response.headers[TRACE_ID_HEADER] = observation.trace_id
        if isinstance(resp, dict):
            return ChatResponse(**resp)
        return ChatResponse(response=resp)
    except Exception:
        logger.exception("Chat request failed (user_id=%s)", current_user.id)
        raise HTTPException(status_code=502, detail="对话服务暂时不可用")


@router.post("/chat/stream")
async def chat_stream_endpoint(request: ChatRequest, current_user: User = Depends(get_current_user),
                               db: Session = Depends(get_db)):
    """跟 Agent 对话 (流式)"""
    scope = list(dict.fromkeys(request.knowledge_ids)) if request.knowledge_ids is not None else None
    visible_document_ids(db, current_user.id, scope)

    async def event_generator():
        try:
            session_id = request.session_id or "default_session"
            async with aclosing(chat_with_agent_stream(
                request.message, current_user.username, current_user.id, session_id,
                knowledge_ids=scope,
            )) as stream:
                async for chunk in stream:
                    yield chunk
        except Exception:
            update_observation(current_trace_root(), level="ERROR", status_message="operation_failed")
            logger.exception("Chat stream failed (user_id=%s)", current_user.id)
            error_data = {"type": "error", "content": "对话服务暂时不可用"}
            yield f"data: {json.dumps(error_data)}\n\n"

    return _ChatStreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
