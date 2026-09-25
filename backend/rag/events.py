"""Request-local RAG events; the sink is bound inside the tool execution thread."""

from contextvars import ContextVar

_sink: ContextVar[tuple | None] = ContextVar("rag_step_sink", default=None)


def bind_rag_step_sink(loop, queue):
    return _sink.set((loop, queue))


def reset_rag_step_sink(token):
    _sink.reset(token)


def emit_rag_step(icon: str, label: str, detail: str = ""):
    sink = _sink.get()
    if sink is None:
        return
    loop, queue = sink
    if not loop.is_closed():
        loop.call_soon_threadsafe(queue.put_nowait, {"icon": icon, "label": label, "detail": detail})
