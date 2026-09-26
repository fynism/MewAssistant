from fastapi import FastAPI, HTTPException as FastAPIHTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pathlib import Path
from contextlib import asynccontextmanager
import time
from uuid import uuid4

from backend import api as api_module
from backend.core.config import settings
from backend.database import init_db
from backend.mcp_knowledge import create_knowledge_mcp_app, knowledge_mcp
from backend.observability import logger, request_id

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"
FRONTEND_ROUTES = {"services/knowledge", "account", "workspace/knowledges", "try"}


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app):
        init_db()
        async with knowledge_mcp.session_manager.run():
            yield

    app = FastAPI(title="呆猫助手 API", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Request log and no-cache headers for development assets.
    @app.middleware("http")
    async def _request_logging_and_no_cache(request, call_next):
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

    # API routes must be registered first so they take priority over the
    # frontend catch-all below.
    app.include_router(api_module.router)
    app.mount("/mcp", create_knowledge_mcp_app())

    # Serve known frontend files. Unknown paths must not masquerade as a
    # successful API response containing the HTML app shell.
    if FRONTEND_DIR.exists():
        @app.get("/", include_in_schema=False)
        async def serve_index():
            return FileResponse(str(FRONTEND_DIR / "index.html"))

        @app.get("/{full_path:path}", include_in_schema=False)
        async def serve_frontend(full_path: str):
            if full_path.rstrip("/") in FRONTEND_ROUTES:
                return FileResponse(str(FRONTEND_DIR / "index.html"))
            file_path = FRONTEND_DIR / full_path
            if file_path.is_file():
                try:
                    file_path.resolve().relative_to(FRONTEND_DIR.resolve())
                except ValueError:
                    raise FastAPIHTTPException(status_code=404)
                return FileResponse(str(file_path))
            raise FastAPIHTTPException(status_code=404)

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port)
