from fastapi import FastAPI, HTTPException as FastAPIHTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pathlib import Path

from backend import api as api_module
from backend.core.config import settings
from backend.database import init_db

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"


def create_app() -> FastAPI:
    app = FastAPI(title="呆猫助手 API")

    @app.on_event("startup")
    async def _startup_init_db():
        init_db()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # No-cache middleware for development
    @app.middleware("http")
    async def _no_cache(request, call_next):
        response = await call_next(request)
        path = request.url.path or ""
        if path == "/" or path.endswith((".html", ".js", ".css")):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    # API routes must be registered first so they take priority over the
    # frontend catch-all below.
    app.include_router(api_module.router)

    # Serve known frontend files. Unknown paths must not masquerade as a
    # successful API response containing the HTML app shell.
    if FRONTEND_DIR.exists():
        @app.get("/", include_in_schema=False)
        async def serve_index():
            return FileResponse(str(FRONTEND_DIR / "index.html"))

        @app.get("/{full_path:path}", include_in_schema=False)
        async def serve_frontend(full_path: str):
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
