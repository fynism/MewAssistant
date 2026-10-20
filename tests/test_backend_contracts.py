from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def read_backend_file(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


class BackendContractTests(unittest.TestCase):
    def test_app_registers_api_router_before_frontend_catch_all(self):
        source = read_backend_file("backend/app.py")

        include_router_pos = source.index("app.include_router(router)")
        catch_all_pos = source.index('register_frontend(app)')

        self.assertLess(include_router_pos, catch_all_pos)

    def test_streaming_endpoint_preserves_sse_error_shape(self):
        source = read_backend_file("backend/api/routers/chat.py")

        self.assertIn('@router.post("/chat/stream")', source)
        self.assertIn('media_type="text/event-stream"', source)
        self.assertIn('"X-Accel-Buffering": "no"', source)
        self.assertIn('{"type": "error", "content": "对话服务暂时不可用"}', source)
        self.assertIn('yield f"data: {json.dumps(error_data)}\\n\\n"', source)

    def test_document_delete_scopes_cleanup_by_document_id(self):
        processing = read_backend_file("backend/services/knowledge/processing.py")
        lifecycle = read_backend_file("backend/services/knowledge/lifecycle.py")
        self.assertIn('expr = f\'document_id == "{item.id}"\'', processing)
        self.assertIn('item.status = "deleting"', lifecycle)

    def test_api_module_aggregates_all_route_modules(self):
        source = read_backend_file("backend/api/router.py")

        self.assertIn("from backend.api.routers import auth, chat, sessions, knowledges, invitations", source)
        self.assertIn("router.include_router(auth.router)", source)
        self.assertIn("router.include_router(sessions.router)", source)
        self.assertIn("router.include_router(chat.router)", source)
        self.assertIn("router.include_router(knowledges.router)", source)
        self.assertIn("router.include_router(invitations.router)", source)
        self.assertNotIn("router.include_router(documents.router)", source)

    def test_rag_retrieval_uses_shared_dependencies(self):
        source = read_backend_file("backend/rag/retrieval.py")

        self.assertIn("from backend.infrastructure import resources", source)
        self.assertNotIn("_milvus_manager = MilvusManager()", source)
        self.assertNotIn("_parent_chunk_store = ParentChunkStore()", source)

    def test_session_routes_do_not_import_agent_storage(self):
        source = read_backend_file("backend/api/routers/sessions.py")

        self.assertIn("from backend.services.chat.storage import conversation_storage as storage", source)
        self.assertNotIn("from backend.services.chat.agent import storage", source)

    def test_environment_reads_are_centralized_in_config(self):
        offenders = []
        for path in (ROOT / "backend").rglob("*.py"):
            relative_path = path.relative_to(ROOT).as_posix()
            if relative_path == "backend/core/config.py":
                continue
            source = path.read_text(encoding="utf-8")
            if "os.getenv" in source or "load_dotenv" in source:
                offenders.append(relative_path)

        self.assertEqual([], offenders)

    def test_rag_pipeline_uses_dedicated_event_module(self):
        source = read_backend_file("backend/rag/pipeline.py")

        self.assertIn("from backend.rag.events import emit_rag_step", source)
        self.assertNotIn("from backend.services.chat.tools import emit_rag_step", source)

    def test_rag_pipeline_uses_split_rag_modules(self):
        source = read_backend_file("backend/rag/pipeline.py")

        self.assertIn("from backend.rag.expansion import generate_hypothetical_document, step_back_expand", source)
        self.assertIn("from backend.rag.retrieval import retrieve_documents", source)
        self.assertNotIn("from backend.rag.utils import", source)


if __name__ == "__main__":
    unittest.main()
