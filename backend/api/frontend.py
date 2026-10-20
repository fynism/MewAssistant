"""Serve the local SPA and its assets after registering API routes."""

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from backend.core.config import BASE_DIR

FRONTEND_DIR = BASE_DIR / "frontend"
FRONTEND_ROUTES = {"services/knowledge", "account", "admin", "workspace/knowledges", "try"}


def register_frontend(app: FastAPI) -> None:
    if not FRONTEND_DIR.exists():
        return

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
                raise HTTPException(status_code=404)
            return FileResponse(str(file_path))
        raise HTTPException(status_code=404)
