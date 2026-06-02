from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


def read_backend_file(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


class BackendContractTests(unittest.TestCase):
    def test_app_registers_api_router_before_frontend_catch_all(self):
        source = read_backend_file("backend/app.py")

        include_router_pos = source.index("app.include_router(api_module.router)")
        catch_all_pos = source.index('@app.get("/{full_path:path}"')

        self.assertLess(include_router_pos, catch_all_pos)

    def test_streaming_endpoint_preserves_sse_error_shape(self):
        source = read_backend_file("backend/routers/chat.py")

        self.assertIn('@router.post("/chat/stream")', source)
        self.assertIn('media_type="text/event-stream"', source)
        self.assertIn('"X-Accel-Buffering": "no"', source)
        self.assertIn('{"type": "error", "content": str(e)}', source)
        self.assertIn('yield f"data: {json.dumps(error_data)}\\n\\n"', source)

    def test_document_delete_removes_bm25_before_milvus_delete(self):
        source = read_backend_file("backend/services/document_service.py")
        match = re.search(
            r"def delete_document\(.*?(?=\n    def |\Z)",
            source,
            flags=re.DOTALL,
        )

        self.assertIsNotNone(match)
        body = match.group(0)
        self.assertLess(
            body.index("self.remove_bm25_stats_for_filename(filename)"),
            body.index("self.milvus_manager.delete(delete_expr)"),
        )

    def test_api_module_aggregates_all_route_modules(self):
        source = read_backend_file("backend/api.py")

        self.assertIn("from backend.routers import auth, chat, documents, sessions", source)
        self.assertIn("router.include_router(auth.router)", source)
        self.assertIn("router.include_router(sessions.router)", source)
        self.assertIn("router.include_router(chat.router)", source)
        self.assertIn("router.include_router(documents.router)", source)

    def test_rag_retrieval_uses_shared_dependencies(self):
        source = read_backend_file("backend/rag_utils.py")

        self.assertIn("from backend.dependencies import", source)
        self.assertNotIn("_milvus_manager = MilvusManager()", source)
        self.assertNotIn("_parent_chunk_store = ParentChunkStore()", source)

    def test_session_routes_do_not_import_agent_storage(self):
        source = read_backend_file("backend/routers/sessions.py")

        self.assertIn("from backend.services.conversation_storage import conversation_storage as storage", source)
        self.assertNotIn("from backend.agent import storage", source)

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
        source = read_backend_file("backend/rag_pipeline.py")

        self.assertIn("from backend.rag.events import emit_rag_step", source)
        self.assertNotIn("from backend.tools import emit_rag_step", source)


if __name__ == "__main__":
    unittest.main()
