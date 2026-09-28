"""Single-process document dispatcher and audit retention loop."""

import asyncio
import time

from backend.core.config import settings
from backend.database import SessionLocal
from backend.models import KnowledgeDocument
from backend.observability import logger
from backend.services.call_audit import prune_expired_audits
from backend.services.knowledge_service import process_document, reset_interrupted_documents


def pending_document_ids(limit: int) -> list[str]:
    with SessionLocal() as db:
        return [row[0] for row in db.query(KnowledgeDocument.id).filter(
            KnowledgeDocument.status.in_(["pending", "replacing"])
        ).order_by(KnowledgeDocument.created_at).limit(limit).all()]


def reset_at_startup() -> int:
    with SessionLocal() as db:
        return reset_interrupted_documents(db)


async def run_document_maintenance() -> None:
    active: dict[str, asyncio.Task] = {}
    try:
        recovered = await asyncio.to_thread(reset_at_startup)
        if recovered:
            logger.warning("document_recovery resumed=%d", recovered)
        last_prune = 0.0
        while True:
            try:
                if time.monotonic() - last_prune >= 24 * 60 * 60:
                    last_prune = time.monotonic()
                    removed = await asyncio.to_thread(prune_expired_audits)
                    if removed:
                        logger.info("audit_retention removed=%d", removed)
                capacity = max(1, settings.max_document_processing) - len(active)
                if capacity > 0:
                    ids = await asyncio.to_thread(pending_document_ids, capacity + len(active))
                    for document_id in ids:
                        if document_id in active:
                            continue
                        task = asyncio.create_task(asyncio.to_thread(process_document, document_id))
                        active[document_id] = task
                        task.add_done_callback(lambda finished, item_id=document_id: _finished(active, item_id, finished))
                        if len(active) >= max(1, settings.max_document_processing):
                            break
            except Exception:
                logger.exception("document_maintenance_failed")
            await asyncio.sleep(max(0.2, settings.document_scan_interval_seconds))
    except asyncio.CancelledError:
        raise
    finally:
        # A process shutdown may interrupt active work. The next startup resets
        # processing rows and reruns ingestion after clearing partial index data.
        for task in active.values():
            task.cancel()


def _finished(active: dict[str, asyncio.Task], document_id: str, task: asyncio.Task) -> None:
    active.pop(document_id, None)
    if not task.cancelled():
        try:
            task.result()
        except Exception:
            logger.exception("document_processing_failed document_id=%s", document_id)
