"""Request correlation and opt-in SQL timing logs."""

import logging
import time
from contextvars import ContextVar

from sqlalchemy import event
from sqlalchemy.engine import Engine


request_id: ContextVar[str] = ContextVar("request_id", default="-")
logger = logging.getLogger("uvicorn.error")


def enable_sql_logging(engine: Engine, include_parameters: bool = False) -> None:
    """Log SQL templates and duration, never user-supplied bound values."""

    @event.listens_for(engine, "before_cursor_execute")
    def before_execute(_connection, _cursor, _statement, _parameters, context, _executemany):
        context._supermew_query_started = time.perf_counter()

    @event.listens_for(engine, "after_cursor_execute")
    def after_execute(_connection, _cursor, statement, parameters, context, _executemany):
        elapsed_ms = (time.perf_counter() - context._supermew_query_started) * 1000
        logger.info(
            "sql request_id=%s duration_ms=%.1f statement=%s",
            request_id.get(), elapsed_ms, " ".join(statement.split())[:2000],
        )

    @event.listens_for(engine, "handle_error")
    def on_error(exception_context):
        context = exception_context.execution_context
        started = getattr(context, "_supermew_query_started", None)
        elapsed_ms = (time.perf_counter() - started) * 1000 if started else 0.0
        logger.warning(
            "sql_error request_id=%s duration_ms=%.1f type=%s",
            request_id.get(), elapsed_ms, type(exception_context.original_exception).__name__,
        )
