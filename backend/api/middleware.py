"""HTTP request tracing and cache policy for development assets."""

import time
from uuid import uuid4

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.frontend import FRONTEND_ROUTES
from backend.core.config import settings
from backend.core.observability import logger, request_id


def register_middleware(app: FastAPI) -> None:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_logging_and_no_cache(request, call_next):
        current_id = uuid4().hex
        token = request_id.set(current_id)
        started = time.perf_counter()
        path = request.url.path or ""
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = current_id
            if path == "/" or path.lstrip("/") in FRONTEND_ROUTES or path.endswith((".html", ".js", ".css")):
                response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
                response.headers["Pragma"] = "no-cache"
                response.headers["Expires"] = "0"
            return response
        finally:
            if settings.log_requests:
                logger.info(
                    "request_id=%s method=%s path=%s status=%d duration_ms=%.1f",
                    current_id, request.method, path, status_code,
                    (time.perf_counter() - started) * 1000,
                )
            request_id.reset(token)
