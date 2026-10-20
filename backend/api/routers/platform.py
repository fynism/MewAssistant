"""Public, non-secret service availability for the platform UI."""

from urllib.parse import urlsplit

from fastapi import APIRouter

from backend.core.config import settings

router = APIRouter(prefix="/platform")


@router.get("/services/knowledge")
def knowledge_service_info():
    base = settings.mcp_public_base_url.strip().rstrip("/")
    parsed = urlsplit(base)
    public_https = parsed.scheme == "https" and bool(parsed.netloc) and not parsed.path
    available = settings.mcp_external_enabled and public_https
    return {"id": "knowledge", "externalAvailable": available,
            "endpoint": f"{base}/mcp/knowledge" if available else None,
            "tools": ["listKnowledges", "retrieve"]}
