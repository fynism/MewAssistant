"""Serve the local SPA and its assets after registering API routes."""

from mimetypes import guess_type
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse

from backend.api.frontend_assets import ASSET_SUFFIXES, Asset, local_file, read_asset
from backend.core.config import BASE_DIR

FRONTEND_DIR = BASE_DIR / "frontend"
FRONTEND_ROUTES = {"services/knowledge", "account", "admin", "workspace/knowledges", "try"}
NO_STORE = {"Cache-Control": "no-store"}
IMMUTABLE = "public, max-age=31536000, immutable"


def _revalidate(request: Request, asset: Asset, cache_control: str, media_type: str) -> Response:
    etag = f'"{asset.version}"'
    headers = {"Cache-Control": cache_control, "ETag": etag}
    validators = ",".join(request.headers.getlist("if-none-match")).split(",")
    if any(value.strip().removeprefix("W/") in ("*", etag) for value in validators):
        return Response(status_code=304, headers=headers)
    return Response(asset.body, headers=headers, media_type=media_type)


def register_frontend(app: FastAPI) -> None:
    if not FRONTEND_DIR.exists():
        return
    root = FRONTEND_DIR.resolve()
    index = root / "index.html"

    @app.get("/", include_in_schema=False)
    def serve_index(request: Request):
        return _revalidate(request, read_asset(root, index), "no-cache", "text/html")

    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_frontend(request: Request, full_path: str):
        if full_path == index.name or full_path.rstrip("/") in FRONTEND_ROUTES:
            return serve_index(request)

        file_path = local_file(root, full_path)
        if file_path is None:
            raise HTTPException(status_code=404, headers=NO_STORE)

        versions = request.query_params.getlist("v")
        if len(versions) == 1 and file_path.suffix.lower() in ASSET_SUFFIXES:
            asset = read_asset(root, file_path)
            if versions[0] == asset.version:
                media_type = guess_type(file_path.name)[0] or "application/octet-stream"
                return _revalidate(request, asset, IMMUTABLE, media_type)

        return FileResponse(file_path, headers=NO_STORE)
