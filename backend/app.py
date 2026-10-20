"""Application composition and the stable ASGI entry point."""

from fastapi import FastAPI
from contextlib import asynccontextmanager
import asyncio

from backend.api.router import router
from backend.api.frontend import register_frontend
from backend.api.middleware import register_middleware
from backend.core.config import settings
from backend.infrastructure.database.session import init_db
from backend.integrations.mcp.knowledge import create_knowledge_mcp_app, knowledge_mcp
from backend.workers.maintenance import run_document_maintenance

def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app):
        init_db()
        maintenance = asyncio.create_task(run_document_maintenance())
        async with knowledge_mcp.session_manager.run():
            try:
                yield
            finally:
                maintenance.cancel()
                await asyncio.gather(maintenance, return_exceptions=True)

    app = FastAPI(title="呆猫助手 API", lifespan=lifespan)

    register_middleware(app)

    # API routes must be registered first so they take priority over the
    # frontend catch-all below.
    app.include_router(router)
    app.mount("/mcp", create_knowledge_mcp_app())

    register_frontend(app)

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port)
