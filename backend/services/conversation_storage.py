from datetime import datetime

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from backend.cache import cache
from backend.database import SessionLocal
from backend.models import ChatMessage, ChatSession, User


class ConversationStorage:
    """对话存储（PostgreSQL + Redis）。"""

    @staticmethod
    def _messages_cache_key(user_id: str, session_id: str) -> str:
        return f"chat_messages:{user_id}:{session_id}"

    @staticmethod
    def _sessions_cache_key(user_id: str) -> str:
        return f"chat_sessions:{user_id}"

    @staticmethod
    def _to_langchain_messages(records: list[dict]) -> list:
        messages = []
        for msg_data in records:
            msg_type = msg_data.get("type")
            content = msg_data.get("content", "")
            reasoning = msg_data.get("reasoning_content")
            if msg_type == "human":
                messages.append(HumanMessage(content=content))
            elif msg_type == "ai":
                additional_kwargs = {}
                if reasoning:
                    additional_kwargs["reasoning_content"] = reasoning
                messages.append(AIMessage(content=content, additional_kwargs=additional_kwargs))
            elif msg_type == "system":
                messages.append(SystemMessage(content=content))
        return messages

    def save(self, user_id: str, session_id: str, messages: list, metadata: dict = None, extra_message_data: list = None):
        """Append the completed turn; existing turns and their scopes stay immutable."""
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.username == user_id).first()
            if not user:
                return

            session = (
                db.query(ChatSession)
                .filter(ChatSession.user_id == user.id, ChatSession.session_id == session_id)
                .first()
            )
            if not session:
                session = ChatSession(user_id=user.id, session_id=session_id, metadata_json=metadata or {})
                db.add(session)
                db.flush()
            else:
                if metadata:
                    session.metadata_json = {**(session.metadata_json or {}), **metadata}

            # The agent may summarize old messages before calling save. Only
            # the new human/AI pair belongs in the database on this call.
            start = max(0, len(messages) - 2)
            now = datetime.utcnow()
            for idx in range(start, len(messages)):
                msg = messages[idx]
                rag_trace = None
                knowledge_ids = None
                if extra_message_data and idx < len(extra_message_data):
                    extra = extra_message_data[idx] or {}
                    rag_trace = extra.get("rag_trace")
                    knowledge_ids = extra.get("knowledge_ids")

                reasoning = msg.additional_kwargs.get("reasoning_content") if hasattr(msg, "additional_kwargs") else None
                db.add(
                    ChatMessage(
                        session_ref_id=session.id,
                        message_type=msg.type,
                        content=str(msg.content),
                        reasoning_content=reasoning,
                        timestamp=now,
                        rag_trace=rag_trace,
                        knowledge_scope_json=knowledge_ids,
                    )
                )

            session.updated_at = now
            db.commit()

            cache.delete(self._messages_cache_key(user_id, session_id))
            cache.delete(self._sessions_cache_key(user_id))
        finally:
            db.close()

    def load(self, user_id: str, session_id: str) -> list:
        """加载对话"""
        cached = cache.get_json(self._messages_cache_key(user_id, session_id))
        if cached is not None:
            return self._to_langchain_messages(cached)

        records = self.get_session_messages(user_id, session_id)
        cache.set_json(self._messages_cache_key(user_id, session_id), records)
        return self._to_langchain_messages(records)

    def list_sessions(self, user_id: str) -> list:
        """列出用户的所有会话"""
        return [item["session_id"] for item in self.list_session_infos(user_id)]

    def list_session_infos(self, user_id: str) -> list[dict]:
        cached = cache.get_json(self._sessions_cache_key(user_id))
        if cached is not None:
            return cached

        db = SessionLocal()
        try:
            user = db.query(User).filter(User.username == user_id).first()
            if not user:
                return []

            sessions = (
                db.query(ChatSession)
                .filter(ChatSession.user_id == user.id)
                .order_by(ChatSession.updated_at.desc())
                .all()
            )
            result = []
            for s in sessions:
                count = db.query(ChatMessage).filter(ChatMessage.session_ref_id == s.id).count()
                result.append(
                    {
                        "session_id": s.session_id,
                        "updated_at": s.updated_at.isoformat(),
                        "message_count": count,
                        "title": s.title,
                        "last_knowledge_ids": (s.metadata_json or {}).get("last_knowledge_ids"),
                        "legacy_scope_unknown": "last_knowledge_ids" not in (s.metadata_json or {}),
                    }
                )
            cache.set_json(self._sessions_cache_key(user_id), result)
            return result
        finally:
            db.close()

    def get_session_messages(self, user_id: str, session_id: str) -> list[dict]:
        cached = cache.get_json(self._messages_cache_key(user_id, session_id))
        if cached is not None:
            return cached

        db = SessionLocal()
        try:
            user = db.query(User).filter(User.username == user_id).first()
            if not user:
                return []
            session = (
                db.query(ChatSession)
                .filter(ChatSession.user_id == user.id, ChatSession.session_id == session_id)
                .first()
            )
            if not session:
                return []

            rows = (
                db.query(ChatMessage)
                .filter(ChatMessage.session_ref_id == session.id)
                .order_by(ChatMessage.id.asc())
                .all()
            )
            result = [
                {
                    "type": row.message_type,
                    "content": row.content,
                    "reasoning_content": row.reasoning_content,
                    "timestamp": row.timestamp.isoformat(),
                    "rag_trace": row.rag_trace,
                    "knowledge_ids": row.knowledge_scope_json,
                }
                for row in rows
            ]
            cache.set_json(self._messages_cache_key(user_id, session_id), result)
            return result
        finally:
            db.close()

    def get_session_metadata(self, user_id: str, session_id: str) -> dict:
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.username == user_id).first()
            if not user:
                return {}
            session = db.query(ChatSession).filter(
                ChatSession.user_id == user.id, ChatSession.session_id == session_id).first()
            return dict(session.metadata_json or {}) if session else {}
        finally:
            db.close()

    def delete_session(self, user_id: str, session_id: str) -> bool:
        """删除指定用户的会话，返回是否删除成功"""
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.username == user_id).first()
            if not user:
                return False
            session = (
                db.query(ChatSession)
                .filter(ChatSession.user_id == user.id, ChatSession.session_id == session_id)
                .first()
            )
            if not session:
                return False

            db.delete(session)
            db.commit()
            cache.delete(self._messages_cache_key(user_id, session_id))
            cache.delete(self._sessions_cache_key(user_id))
            return True
        finally:
            db.close()

    def update_title(self, user_id: str, session_id: str, title: str):
        """更新会话标题，同时刷新 Redis 会话列表缓存"""
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.username == user_id).first()
            if not user:
                return
            session = (
                db.query(ChatSession)
                .filter(ChatSession.user_id == user.id, ChatSession.session_id == session_id)
                .first()
            )
            if session:
                session.title = title
                db.commit()
                cache.delete(self._sessions_cache_key(user_id))
        finally:
            db.close()


conversation_storage = ConversationStorage()
