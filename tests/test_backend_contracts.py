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


if __name__ == "__main__":
    unittest.main()
