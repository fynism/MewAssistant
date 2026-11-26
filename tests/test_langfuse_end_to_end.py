import asyncio
import io
import json
import os
import tempfile
import unittest
from contextlib import ExitStack, contextmanager, redirect_stdout
from dataclasses import replace
from importlib import import_module
from importlib.util import find_spec
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import httpx
import httpx2
from langchain_core.messages import AIMessageChunk
from langfuse import Langfuse
from langfuse.api.client import AsyncLangfuseAPI
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.core.security import create_access_token
from backend.infrastructure.database.models import KnowledgeBase, KnowledgeDocument, User
from backend.infrastructure.database.session import Base
from backend.services.platform.api_keys import create_key


QUERY = "fixture-private-query"
CONTEXT = "fixture-private-chunk"
ANSWER = "fixture-private-answer"
ERROR_DETAIL = "fixture-private-reranker-error"
FILENAME = "fixture-private-filename.txt"


class FakeChatAgent:
    def __init__(self, model, tools, system_prompt):
        self.search = next(tool for tool in tools if getattr(tool, "name", None) == "search_knowledge_base")

    async def astream(self, *_args, **_kwargs):
        await asyncio.to_thread(self.search.invoke, {"query": QUERY})
        yield AIMessageChunk(content=ANSWER), {}


class LangfuseEndToEndTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from backend.api import dependencies
        from backend.core import langfuse_tracing
        from backend.integrations.mcp import knowledge
        from backend.rag import pipeline, rerank, retrieval
        from backend.services.chat import agent

        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        mock = self.stack.enter_context
        directory = mock(tempfile.TemporaryDirectory())
        engine = create_engine(f"sqlite:///{Path(directory) / 'test.sqlite'}")
        self.stack.callback(engine.dispose)
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as db:
            db.add_all([
                User(id=1, username="alice", password_hash="unused", role="user"),
                KnowledgeBase(id="fixture-kb", owner_id=1, name="Fixture KB"),
                KnowledgeDocument(id="fixture-doc", knowledge_id="fixture-kb", filename=FILENAME,
                                  storage_key="fixture.txt", file_type=".txt", status="ready"),
            ])
            db.commit()
            _, self.mcp_key = create_key(db, 1, "Testing")
        self.token = create_access_token("alice", "user")
        mock(patch.object(dependencies, "SessionLocal", factory))
        mock(patch.object(knowledge, "SessionLocal", factory))
        mock(patch.object(knowledge, "record_call"))
        mock(patch.object(knowledge, "enforce_rate_limits"))
        mock(patch.object(knowledge, "settings", replace(knowledge.settings, mcp_external_enabled=True)))
        mock(patch.object(retrieval, "SessionLocal", factory))
        mock(patch.object(retrieval.resources.embedding_service, "get_embeddings", return_value=[[0.1]]))
        mock(patch.object(retrieval.resources.embedding_service, "get_sparse_embedding", return_value={1: 1.0}))
        mock(patch.object(retrieval.resources.milvus_manager, "hybrid_retrieve", side_effect=lambda **_: [
            {"document_id": "fixture-doc", "chunk_id": "fixture-chunk", "text": CONTEXT,
             "filename": FILENAME, "page_number": 1}
        ]))
        mock(patch.object(rerank, "RERANK_API_KEY", "fixture-private-reranker-key"))
        mock(patch.object(rerank, "RERANK_MODEL", "fixture-reranker"))
        mock(patch.object(rerank, "RERANK_BINDING_HOST", "https://rerank.invalid"))
        mock(patch.object(rerank.requests, "post", return_value=SimpleNamespace(
            status_code=429, text=ERROR_DETAIL)))
        mock(patch.object(pipeline, "_get_grader_model", return_value=SimpleNamespace(
            invoke=lambda _: SimpleNamespace(content="yes"))))
        mock(patch.object(agent, "create_agent", side_effect=FakeChatAgent))
        mock(patch.object(agent.storage, "load", return_value=[]))
        mock(patch.object(agent.storage, "save"))
        mock(patch.object(agent, "generate_session_title", return_value=""))

        self.exporter = InMemorySpanExporter()
        self.tracing = langfuse_tracing

        app_module = import_module("backend.app")
        mock(patch.object(app_module, "init_db"))
        mock(patch.object(app_module, "run_document_maintenance", lambda: asyncio.sleep(3600)))
        # Each mounted MCP app has its own single-use session manager.
        mock(patch.object(knowledge.knowledge_mcp._lowlevel_server, "_session_manager"))
        self.app = app_module.create_app()

    @contextmanager
    def use_langfuse(self, *, enabled=True, capture=False):
        factory = self.tracing.get_tracing_client
        config = replace(
            self.tracing.settings, langfuse_tracing_enabled=enabled,
            langfuse_capture_retrieval_content=capture, langfuse_base_url="https://langfuse.invalid",
            langfuse_public_key=f"pk-lf-{uuid4()}", langfuse_secret_key=f"sk-lf-{uuid4()}",
        )
        factory.cache_clear()
        with (patch.object(self.tracing, "settings", config),
              patch.dict(os.environ, {"LANGFUSE_TRACING_ENABLED": "true"}),
              patch("langfuse.Langfuse", side_effect=lambda **kwargs: Langfuse(
                  span_exporter=self.exporter, flush_interval=3600, **kwargs)) as constructor):
            try:
                yield constructor
            finally:
                if constructor.called and (client := factory()) is not None:
                    client.shutdown()
                factory.cache_clear()

    def use_asgi_client(self):
        transport = httpx2.ASGITransport(app=self.app)
        async_client = httpx2.AsyncClient
        return patch.object(httpx2, "AsyncClient", side_effect=lambda **kwargs: async_client(
            transport=transport, **kwargs))

    async def test_concurrent_mcp_and_chat_scores_target_their_server_traces(self):
        from benchmarks import rag_eval

        scores = []

        def accept_scores(request):
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.url.path, "/api/public/scores")
            scores.extend(json.loads(request.content))
            return httpx.Response(202, json={"message": "Accepted"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(accept_scores)) as score_http:
            score_client = SimpleNamespace(async_api=AsyncLangfuseAPI(
                base_url="https://langfuse.invalid", httpx_client=score_http))
            with (self.use_langfuse() as sdk, self.use_asgi_client(),
                  patch.object(rag_eval, "get_client", return_value=score_client),
                  patch.dict(os.environ, {"MCP_API_KEY": self.mcp_key, "SUPERMEW_BENCHMARK_TOKEN": self.token,
                                          "LANGFUSE_PUBLIC_KEY": "fixture-public", "LANGFUSE_SECRET_KEY": "fixture-secret"}),
                  redirect_stdout(io.StringIO()) as output):
                async with self.app.router.lifespan_context(self.app):
                    parser = rag_eval.build_parser()
                    arguments = ["--base-url", "https://testserver", "--query", QUERY,
                                 "--knowledge-id", "fixture-kb"]
                    await asyncio.gather(*(rag_eval.run(parser.parse_args([channel, *arguments]))
                                           for channel in ("mcp", "chat")))
                    self.assertEqual(self.exporter.get_finished_spans(), ())
                sdk.assert_called_once()

        spans = self.exporter.get_finished_spans()
        root_trace_ids = {span.name: f"{span.context.trace_id:032x}"
                          for span in spans if span.name in {"mcp.retrieve", "chat.stream"}}
        self.assertEqual(set(root_trace_ids), {"mcp.retrieve", "chat.stream"})
        self.assertEqual(len(set(root_trace_ids.values())), 2)
        self.assertEqual(len(scores), 2)
        self.assertEqual({score["traceId"] for score in scores}, set(root_trace_ids.values()))
        self.assertEqual({score["name"] for score in scores}, {"rag_eval_transport_smoke"})

        for trace_id in root_trace_ids.values():
            child_names = {span.name for span in spans if f"{span.context.trace_id:032x}" == trace_id}
            self.assertTrue({"rag.retrieve", "rag.embed", "rag.search", "rag.rerank"} <= child_names)
        self.assertTrue(any(span.name == "chat.agent" for span in spans))
        for span in spans:
            self.assertNotIn("langfuse.observation.input", span.attributes)
            self.assertNotIn("langfuse.observation.output", span.attributes)
            if span.name == "rag.rerank":
                metadata = span.attributes
                self.assertTrue(json.loads(metadata["langfuse.observation.metadata.rerank_enabled"]))
                self.assertFalse(json.loads(metadata["langfuse.observation.metadata.rerank_succeeded"]))
                self.assertEqual(metadata["langfuse.observation.status_message"], "rerank_fallback")
        wire_data = repr([(dict(span.attributes), [(event.name, dict(event.attributes or {})) for event in span.events],
                           span.status.description) for span in spans])
        for private_value in (QUERY, CONTEXT, ANSWER, FILENAME, ERROR_DETAIL, self.mcp_key, self.token):
            self.assertNotIn(private_value, wire_data)
            self.assertNotIn(private_value, output.getvalue())

    async def test_retrieval_content_is_exported_only_when_explicitly_enabled(self):
        from benchmarks.rag_eval import retrieve_mcp, stream_chat

        with self.use_langfuse(capture=True), self.use_asgi_client():
            async with self.app.router.lifespan_context(self.app):
                result = await retrieve_mcp("https://testserver", self.mcp_key, QUERY, ["fixture-kb"], 5)
                await stream_chat("https://testserver", self.token, QUERY, ["fixture-kb"])

        spans = self.exporter.get_finished_spans()
        for field, body in (("input", {"query": QUERY}), ("output", {"retrieved_contexts": [CONTEXT]})):
            attribute = f"langfuse.observation.{field}"
            captured = {span.name: json.loads(span.attributes[attribute]) for span in spans
                        if attribute in span.attributes}
            self.assertEqual(captured, {"mcp.retrieve": body, "rag.graph": body})
        self.assertEqual(result["results"][0]["text"], CONTEXT)

    async def test_disabled_tracing_leaves_chat_and_mcp_responses_usable(self):
        from benchmarks.rag_eval import retrieve_mcp, stream_chat

        with self.use_langfuse(enabled=False) as sdk, self.use_asgi_client():
            async with self.app.router.lifespan_context(self.app):
                result = await retrieve_mcp("https://testserver", self.mcp_key, QUERY, ["fixture-kb"], 5)
                trace_id, answer, contexts = await stream_chat(
                    "https://testserver", self.token, QUERY, ["fixture-kb"])

        self.assertEqual(result["results"][0]["text"], CONTEXT)
        self.assertNotIn("traceId", result)
        self.assertIsNone(trace_id)
        self.assertEqual(answer, ANSWER)
        self.assertIn(CONTEXT, contexts[0])
        self.assertEqual(self.exporter.get_finished_spans(), ())
        sdk.assert_not_called()



    async def test_failed_chat_reports_a_safe_trace_and_writes_no_score(self):
        from backend.services.chat import agent
        from benchmarks import rag_eval

        class FailingAgent:
            async def astream(self, *_args, **_kwargs):
                yield AIMessageChunk(content=ANSWER), {}
                raise RuntimeError(ERROR_DETAIL)

        with (self.use_langfuse(), self.use_asgi_client(),
              patch.object(agent, "create_agent", return_value=FailingAgent()),
              patch.object(agent.logger, "exception"), patch.object(rag_eval, "get_client") as score_client,
              patch.dict(os.environ, {"SUPERMEW_BENCHMARK_TOKEN": self.token,
                                      "LANGFUSE_PUBLIC_KEY": "fixture-public", "LANGFUSE_SECRET_KEY": "fixture-secret"})):
            async with self.app.router.lifespan_context(self.app):
                args = rag_eval.build_parser().parse_args([
                    "chat", "--base-url", "https://testserver", "--query", QUERY,
                    "--knowledge-id", "fixture-kb",
                ])
                with self.assertRaisesRegex(rag_eval.EvaluationError, "chat stream reported an error"):
                    await rag_eval.run(args)

        score_client.assert_not_called()
        spans = {span.name: span for span in self.exporter.get_finished_spans()}
        for name in ("chat.stream", "chat.agent"):
            span = spans[name]
            self.assertEqual(span.attributes["langfuse.observation.level"], "ERROR")
            self.assertNotIn(ERROR_DETAIL, repr(dict(span.attributes)))
            self.assertFalse(span.events)


class RagasEvaluationTests(unittest.IsolatedAsyncioTestCase):
    @unittest.skipUnless(find_spec("ragas"), "requires the optional eval dependency group")
    async def test_ragas_document_recall_deduplicates_document_ids(self):
        from benchmarks.rag_eval import document_recall

        hits = [{"documentId": "fixture-doc"}, {"documentId": "fixture-doc"}, {"documentId": "other"}]
        self.assertEqual(await document_recall(hits, ["fixture-doc", "unseen"]), 0.5)
