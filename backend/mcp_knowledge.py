"""Official MCP Streamable HTTP adapter for the private knowledge service."""

import json
import time
from urllib.parse import urlsplit

from fastapi import HTTPException
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from backend.core.config import settings
from backend.database import SessionLocal
from backend.services.api_keys import authenticate_key
from backend.services.knowledge_tools import list_knowledges, retrieve
from backend.services.rate_limits import enforce_rate_limits
from backend.services.call_audit import record_call


class KnowledgeItem(BaseModel):
    id: str
    name: str
    description: str
    hasReadyDocuments: bool
    readyDocumentCount: int


class KnowledgeList(BaseModel):
    items: list[KnowledgeItem]
    nextCursor: str | None


class RetrievalHit(BaseModel):
    rank: int
    sourceId: str
    knowledgeId: str
    knowledgeName: str
    documentId: str
    filename: str
    pageNumber: int | None = None
    text: str
    retrievalScore: float | None = None
    rerankScore: float | None = None


class RetrievalResult(BaseModel):
    status: str
    results: list[RetrievalHit]


knowledge_mcp = MCPServer(name="SuperMew Knowledge", version="0.1.0",
                          instructions="Use listKnowledges to get your knowledge IDs, then retrieve with explicit knowledgeIds.")


def _bearer(headers) -> str | None:
    value = headers.get("authorization", "") if headers else ""
    parts = value.split(" ", 1)
    return parts[1] if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1] else None


def _invoke(headers, operation, **kwargs):
    started = time.perf_counter()
    token = _bearer(headers)
    if token is None:
        raise ToolError("认证失败")
    with SessionLocal() as db:
        owner_id = authenticate_key(db, token)
        if owner_id is None:
            raise ToolError("认证失败")
        category = "success"
        try:
            return operation(db, owner_id, **kwargs)
        except HTTPException as exc:
            category = ("permission" if exc.status_code == 404 else
                        "invalid_input" if exc.status_code in (400, 422) else "service_error")
            if exc.status_code in (400, 404, 422):
                raise ToolError("参数无效或知识库不可访问") from None
            raise ToolError("检索服务暂时不可用") from None
        except Exception:
            category = "service_error"
            raise ToolError("检索服务暂时不可用") from None
        finally:
            tool_name = "listKnowledges" if operation is list_knowledges else operation.__name__
            record_call(owner_id, token[4:].split("_", 1)[0],
                        tool_name, category, int((time.perf_counter() - started) * 1000))


@knowledge_mcp.tool(name="listKnowledges", description="列出当前凭证所有者的私有知识库及可检索状态。", structured_output=True)
async def mcp_list_knowledges(ctx: Context, limit: int = Field(default=50, ge=1, le=100),
                              cursor: str | None = None) -> KnowledgeList:
    data = await run_in_threadpool(_invoke, ctx.headers, list_knowledges, limit=limit, cursor=cursor)
    return KnowledgeList.model_validate(data)


@knowledge_mcp.tool(name="retrieve", description="仅在显式指定的私有知识库中检索原始片段，不生成回答。", structured_output=True)
async def mcp_retrieve(ctx: Context, query: str = Field(min_length=1, max_length=500),
                       knowledgeIds: list[str] = Field(min_length=1, max_length=20,
                                                       json_schema_extra={"uniqueItems": True}),
                       topK: int = Field(default=min(5, settings.max_retrieval_results), ge=1,
                                         le=settings.max_retrieval_results)) -> RetrievalResult:
    if len(set(knowledgeIds)) != len(knowledgeIds):
        raise ToolError("knowledgeIds 不能重复")
    data = await run_in_threadpool(_invoke, ctx.headers, retrieve,
                                   query=query, knowledge_ids=knowledgeIds, top_k=topK)
    # Bound client context even if a future retriever returns oversized chunks.
    for item in data["results"]:
        item["text"] = item["text"][:4000]
    encoded = json.dumps(data, ensure_ascii=False)
    if len(encoded) > 120_000:
        data["results"] = data["results"][:5]
        for item in data["results"]:
            item["text"] = item["text"][:2000]
    return RetrievalResult.model_validate(data)


class ApiKeyMcpAuth:
    """Authenticate every HTTP request before the SDK processes any message."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {key.decode("latin-1").lower(): value.decode("latin-1")
                   for key, value in scope.get("headers", [])}
        origin = headers.get("origin")
        allowed_origins = {value.strip() for value in settings.mcp_allowed_origins.split(",") if value.strip()}
        if origin and origin not in allowed_origins:
            record_call(None, None, "mcp_request", "permission", 0)
            await self._reject(send, 403)
            return
        if not settings.mcp_external_enabled:
            record_call(None, None, "mcp_request", "service_error", 0)
            await self._reject(send, 503)
            return
        token = _bearer(headers)
        if token is None:
            record_call(None, None, "mcp_request", "authentication", 0)
            await self._reject(send, 401)
            return
        try:
            with SessionLocal() as db:
                owner_id = authenticate_key(db, token)
        except Exception:
            record_call(None, None, "mcp_request", "service_error", 0)
            await self._reject(send, 503)
            return
        if owner_id is None:
            record_call(None, None, "mcp_request", "authentication", 0)
            await self._reject(send, 401)
            return
        try:
            enforce_rate_limits(
                (f"mcp:user:{owner_id}", getattr(settings, "mcp_user_rate_per_minute", 60)),
                (f"mcp:key:{token[4:].split('_', 1)[0]}", getattr(settings, "mcp_key_rate_per_minute", 30)))
        except HTTPException as exc:
            record_call(owner_id, token[4:].split("_", 1)[0], "mcp_request",
                        "rate_limited" if exc.status_code == 429 else "service_error", 0)
            await self._reject(send, exc.status_code)
            return
        await self.app(scope, receive, send)

    @staticmethod
    async def _reject(send, status):
        messages = {401: b'{"error":"Authentication required"}',
                    403: b'{"error":"Origin not allowed"}',
                    429: b'{"error":"Rate limit exceeded"}',
                    503: b'{"error":"MCP service temporarily unavailable"}'}
        body = messages.get(status, b'{"error":"MCP access denied"}')
        headers = [(b"content-type", b"application/json")]
        if status == 401:
            headers.append((b"www-authenticate", b"Bearer"))
        if status == 429:
            headers.append((b"retry-after", b"60"))
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})


def create_knowledge_mcp_app():
    hosts = [value.strip() for value in settings.mcp_allowed_hosts.split(",") if value.strip()]
    if settings.mcp_public_base_url:
        host = urlsplit(settings.mcp_public_base_url).netloc
        if host and host not in hosts:
            hosts.append(host)
    security = TransportSecuritySettings(allowed_hosts=hosts,
        allowed_origins=[value.strip() for value in settings.mcp_allowed_origins.split(",") if value.strip()])
    return ApiKeyMcpAuth(knowledge_mcp.streamable_http_app(
        streamable_http_path="/knowledge", stateless_http=True,
        json_response=True, transport_security=security))
