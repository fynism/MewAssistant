"""Run one real retrieval or chat request and score its server-side Langfuse trace."""

import argparse
import asyncio
import json
import math
import os
import re
import sys
from urllib.parse import urlsplit
from uuid import uuid4

import httpx2
from langfuse import get_client
from langfuse.api.scores import CreateScoreRequest

TRACE_HEADER = "X-Langfuse-Trace-Id"
SMOKE_SCORE = "rag_eval_transport_smoke"


class EvaluationError(Exception):
    """A diagnostic safe to print without request or response contents."""


def required_env(name):
    value = os.getenv(name, "").strip()
    if not value:
        raise EvaluationError(f"Set {name} in the local environment.")
    return value


async def retrieve_mcp(base_url, api_key, query, knowledge_ids, top_k):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    headers = {"Authorization": f"Bearer {api_key}"}
    async with httpx2.AsyncClient(headers=headers, timeout=120) as http:
        async with streamable_http_client(f"{base_url}/mcp/knowledge", http_client=http) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                result = await session.call_tool("retrieve", {
                    "query": query, "knowledgeIds": knowledge_ids, "topK": top_k,
                })

    payload = result.structured_content
    if result.is_error:
        raise EvaluationError("The MCP retrieve tool returned an error.")
    if not isinstance(payload, dict) or payload.get("status") not in {"ok", "no_match", "no_documents"}:
        raise EvaluationError("The MCP retrieve response has an invalid status.")
    return payload


async def stream_chat(base_url, token, query, knowledge_ids):
    headers = {"Authorization": f"Bearer {token}", "Accept": "text/event-stream"}
    # A new session avoids evaluating an answer influenced by earlier turns.
    request = {
        "message": query, "knowledge_ids": knowledge_ids,
        "session_id": f"rag-eval-{uuid4().hex}",
    }
    answer = []
    contexts = []
    frame = []
    done = False

    async with httpx2.AsyncClient(headers=headers, timeout=120) as http:
        async with http.stream("POST", f"{base_url}/chat/stream", json=request) as response:
            response.raise_for_status()
            trace_id = response.headers.get(TRACE_HEADER)
            async for line in response.aiter_lines():
                if line.startswith("data:"):
                    frame.append(line[5:].removeprefix(" "))
                if line or not frame:
                    continue
                payload = "\n".join(frame)
                frame.clear()
                if payload == "[DONE]":
                    done = True
                    continue
                event = json.loads(payload)
                if event["type"] == "error":
                    raise EvaluationError("The chat stream reported an error.")
                if event["type"] == "content":
                    answer.append(event["content"])
                elif event["type"] == "trace":
                    chunks = event["rag_trace"].get("retrieved_chunks") or []
                    contexts = [
                        f"[{i}] {chunk.get('filename', 'Unknown')} "
                        f"(Page {chunk.get('page_number', 'N/A')}):\n{chunk.get('text', '')}"
                        for i, chunk in enumerate(chunks, 1)
                    ]

    response_text = "".join(answer)
    if not done or not response_text.strip():
        raise EvaluationError("The chat stream ended without a complete answer.")
    return trace_id, response_text, contexts


async def document_recall(results, reference_ids):
    from ragas.dataset_schema import SingleTurnSample
    from ragas.metrics import IDBasedContextRecall

    sample = SingleTurnSample(
        retrieved_context_ids=list(dict.fromkeys(item["documentId"] for item in results)),
        reference_context_ids=list(dict.fromkeys(reference_ids)),
    )
    return float(await IDBasedContextRecall().single_turn_ascore(sample))


async def faithfulness(query, response, contexts):
    from openai import AsyncOpenAI
    from ragas.llms import llm_factory
    from ragas.metrics.collections import Faithfulness

    if not contexts:
        raise EvaluationError("Faithfulness requires a chat turn with retrieved contexts.")
    async with AsyncOpenAI(
        api_key=required_env("RAGAS_JUDGE_API_KEY"),
        base_url=os.getenv("RAGAS_JUDGE_BASE_URL") or "https://api.openai.com/v1",
        timeout=60,
    ) as client:
        model = llm_factory(required_env("RAGAS_JUDGE_MODEL"), client=client)
        result = await Faithfulness(llm=model).ascore(
            user_input=query, response=response, retrieved_contexts=contexts,
        )
    return float(result.value)


async def write_scores(trace_id, scores):
    requests = [
        CreateScoreRequest(
            name=name, value=value, trace_id=trace_id, data_type="NUMERIC",
            environment=os.getenv("LANGFUSE_TRACING_ENVIRONMENT") or None,
        )
        for name, value in scores.items()
    ]
    result = await get_client().async_api.scores.create(request=requests)
    if getattr(result, "rejected", 0):
        raise EvaluationError(f"Langfuse rejected {result.rejected} score(s).")


async def run(args):
    required_env("LANGFUSE_PUBLIC_KEY")
    required_env("LANGFUSE_SECRET_KEY")
    if args.transport == "mcp":
        payload = await retrieve_mcp(
            args.base_url, required_env("MCP_API_KEY"), args.query, args.knowledge_id, args.top_k,
        )
        trace_id, results = payload.get("traceId"), payload["results"]
        print(f"MCP retrieval completed ({payload['status']}, {len(results)} chunks).", flush=True)
    else:
        trace_id, answer, contexts = await stream_chat(
            args.base_url, required_env("SUPERMEW_BENCHMARK_TOKEN"), args.query, args.knowledge_id,
        )
        print(f"Chat completed ({len(contexts)} context chunks).", flush=True)

    if not isinstance(trace_id, str) or not re.fullmatch(r"[0-9a-f]{32}", trace_id) or int(trace_id, 16) == 0:
        raise EvaluationError("The response has no valid Langfuse trace ID. Check tracing on the target server.")
    print(f"Trace ID: {trace_id}", flush=True)

    scores = {SMOKE_SCORE: 1.0}
    try:
        if args.relevant_document_id:
            scores["ragas_document_recall"] = await document_recall(results, args.relevant_document_id)
        if args.faithfulness:
            scores["ragas_faithfulness"] = await faithfulness(args.query, answer, contexts)
        if not all(math.isfinite(value) for value in scores.values()):
            raise EvaluationError("Ragas produced no finite score for this sample.")
    except EvaluationError:
        raise
    except Exception as exc:
        raise EvaluationError(f"Ragas evaluation failed ({type(exc).__name__}).") from None
    try:
        await write_scores(trace_id, scores)
    except EvaluationError:
        raise
    except Exception as exc:
        raise EvaluationError(f"Langfuse score upload failed ({type(exc).__name__}).") from None
    print("Langfuse accepted " + ", ".join(f"{name}={value:.4g}" for name, value in scores.items()))


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("transport", choices=["mcp", "chat"])
    parser.add_argument("--base-url", required=True, help="SuperMew server URL, without the tool or chat path.")
    parser.add_argument("--query", required=True, help="One query from a test knowledge base.")
    parser.add_argument("--knowledge-id", action="append", required=True, help="Test knowledge base ID. Repeat for multiple.")
    parser.add_argument("--top-k", type=int, default=5, help="MCP result limit.")
    parser.add_argument("--relevant-document-id", action="append", help="MCP reference document ID. Repeat for multiple.")
    parser.add_argument("--faithfulness", action="store_true", help="Send chat test content to the configured Ragas judge.")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    args.base_url = args.base_url.rstrip("/")
    url = urlsplit(args.base_url)
    local_http = url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"}
    if (not url.hostname or (url.scheme != "https" and not local_http)
            or url.username or url.password or url.query or url.fragment):
        parser.error("--base-url must use HTTPS, or HTTP on loopback, without credentials or query parameters.")
    if args.top_k < 1:
        parser.error("--top-k must be positive.")
    if args.faithfulness and args.transport != "chat":
        parser.error("--faithfulness requires chat.")
    if args.relevant_document_id and args.transport != "mcp":
        parser.error("--relevant-document-id requires mcp.")
    try:
        asyncio.run(run(args))
    except EvaluationError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except httpx2.HTTPStatusError as exc:
        print(f"SuperMew returned HTTP {exc.response.status_code}.", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"SuperMew request failed ({type(exc).__name__}).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
