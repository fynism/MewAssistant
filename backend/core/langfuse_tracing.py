"""Optional, metadata-only tracing for authenticated RAG operations."""

from asyncio import CancelledError
from contextlib import contextmanager, ExitStack
from contextvars import ContextVar
from functools import lru_cache
from itertools import islice
import logging

from backend.core.config import settings


logger = logging.getLogger("uvicorn.error")
_trace_root = ContextVar("langfuse_trace_root", default=None)
TRACE_ID_HEADER = "X-Langfuse-Trace-Id"


def _should_export_span(span) -> bool:
    return bool(span.instrumentation_scope and span.instrumentation_scope.name == "langfuse-sdk")


@lru_cache(maxsize=1)
def get_tracing_client():
    if not settings.langfuse_tracing_enabled:
        return None
    if not (settings.langfuse_public_key and settings.langfuse_secret_key and settings.langfuse_base_url):
        logger.warning("Langfuse tracing requires a base URL and both project keys")
        return None
    try:
        from langfuse import Langfuse
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.sampling import ALWAYS_ON

        return Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            base_url=settings.langfuse_base_url,
            tracing_enabled=True,
            # Every reported trace ID must be usable for external evaluation.
            sample_rate=1.0,
            tracer_provider=TracerProvider(sampler=ALWAYS_ON),
            should_export_span=_should_export_span,
        )
    except Exception:
        logger.warning("Langfuse tracing initialization failed")
        return None


def current_trace_root():
    return _trace_root.get()


def update_observation(observation, **attributes) -> None:
    if observation is not None:
        try:
            observation.update(**attributes)
        except Exception:
            logger.warning("Langfuse observation update failed")


@contextmanager
def trace_observation(name, *, as_type="span", root=False, metadata=None):
    current_root = current_trace_root()
    client = get_tracing_client() if root or current_root is not None else None
    if client is None:
        yield None
        return

    stack = ExitStack()
    try:
        if root:
            from opentelemetry import context

            # Each endpoint owns its trace and ignores inbound trace baggage.
            stack.callback(context.detach, context.attach(context.Context()))
        observation = stack.enter_context(client.start_as_current_observation(
            name=name, as_type=as_type, metadata=metadata,
        ))
    except Exception:
        stack.close()
        logger.warning("Langfuse observation could not start")
        yield None
        return

    token = _trace_root.set(observation if root else current_root)
    try:
        yield observation
    except BaseException as exc:
        if isinstance(exc, (CancelledError, GeneratorExit)):
            update_observation(observation, level="WARNING", status_message="cancelled")
        else:
            update_observation(observation, level="ERROR", status_message="operation_failed")
        # Closing the SDK context below without the exception prevents OTel
        # attaching the exception message and stack to the exported span.
        raise
    finally:
        _trace_root.reset(token)
        try:
            stack.close()
        except Exception:
            logger.warning("Langfuse observation could not close")


def capture_retrieval_content(observation, *, query: str | None = None, texts=None) -> None:
    if observation is None or not settings.langfuse_capture_retrieval_content:
        return
    if query is not None:
        update_observation(observation, input={"query": query[:500]})
    if texts is not None:
        update_observation(observation, output={
            "retrieved_contexts": [text[:4000] for text in islice(texts, 20)],
        })


def flush_tracing(client) -> None:
    if client is not None:
        try:
            client.flush()
        except Exception:
            logger.warning("Langfuse tracing flush failed")
