"""Durable background-job queue (Postgres-backed, no external broker).

enqueue() inserts a row (committed with the caller's transaction); a single
worker loop claims jobs with FOR UPDATE SKIP LOCKED, dispatches by job_type to a
registered handler, and retries with exponential backoff up to max_attempts.

Why Postgres and not Redis/arq: one box, jobs are low-volume best-effort
enrichment, and reusing the existing DB means durability with zero new infra.
"""
import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session
from app.models.background_job import BackgroundJob

logger = logging.getLogger(__name__)

# job_type → async handler(payload: dict). Handlers reload everything they need
# from the (serializable) payload, so a job survives a process restart.
JobHandler = Callable[[dict], Awaitable[None]]
_HANDLERS: dict[str, JobHandler] = {}

# Worker tuning.
POLL_INTERVAL_SECONDS = 2.0
BACKOFF_BASE_SECONDS = 30          # 30s, 60s, 120s, ... per attempt
STUCK_JOB_RECLAIM_MINUTES = 15     # running jobs older than this are requeued


def register_job(job_type: str) -> Callable[[JobHandler], JobHandler]:
    def _wrap(fn: JobHandler) -> JobHandler:
        _HANDLERS[job_type] = fn
        return fn
    return _wrap


async def enqueue(
    db: AsyncSession,
    job_type: str,
    payload: dict,
    *,
    tenant_id=None,
    max_attempts: int = 5,
) -> None:
    """Add a job using the caller's session — it commits with the caller, so the
    job only becomes visible once the triggering data is durably committed."""
    db.add(BackgroundJob(
        id=uuid.uuid4(),
        tenant_id=uuid.UUID(str(tenant_id)) if tenant_id else None,
        job_type=job_type,
        payload=payload or {},
        max_attempts=max_attempts,
    ))


async def _claim_one(db: AsyncSession) -> BackgroundJob | None:
    """Atomically claim the next runnable job (skip rows locked by other
    workers). Also reclaims jobs stuck in 'running' past the reclaim window."""
    now = datetime.now(timezone.utc)
    stuck_before = now - timedelta(minutes=STUCK_JOB_RECLAIM_MINUTES)
    row = (await db.execute(text("""
        UPDATE background_jobs SET status='running', locked_at=:now, updated_at=:now
        WHERE id = (
            SELECT id FROM background_jobs
            WHERE (status='pending' AND run_after <= :now)
               OR (status='running' AND locked_at < :stuck)
            ORDER BY run_after
            FOR UPDATE SKIP LOCKED
            LIMIT 1
        )
        RETURNING id
    """), {"now": now, "stuck": stuck_before})).first()
    if not row:
        return None
    return (await db.execute(
        text("SELECT * FROM background_jobs WHERE id = :id"), {"id": row[0]}
    )).mappings().first()


async def _run_one() -> bool:
    """Claim and execute one job. Returns True if a job was handled."""
    async with async_session() as db:
        job = await _claim_one(db)
        await db.commit()
        if not job:
            return False

    job_id = job["id"]
    handler = _HANDLERS.get(job["job_type"])
    if handler is None:
        await _finish(job_id, status="failed", error=f"no handler for {job['job_type']}")
        logger.error("background job %s: no handler for %r", job_id, job["job_type"])
        return True

    try:
        await handler(job["payload"] or {})
        await _finish(job_id, status="succeeded")
    except Exception as exc:  # noqa: BLE001 — record and (maybe) retry
        attempts = (job["attempts"] or 0) + 1
        if attempts >= (job["max_attempts"] or 5):
            await _finish(job_id, status="failed", error=str(exc)[:1000], attempts=attempts)
            logger.exception("background job %s (%s) failed permanently", job_id, job["job_type"])
        else:
            backoff = BACKOFF_BASE_SECONDS * (2 ** (attempts - 1))
            await _retry(job_id, attempts, str(exc)[:1000], backoff)
            logger.warning("background job %s (%s) failed, retry %d in %ds: %s",
                           job_id, job["job_type"], attempts, backoff, exc)
    return True


async def _finish(job_id, *, status: str, error: str | None = None, attempts: int | None = None) -> None:
    async with async_session() as db:
        sets = "status=:status, last_error=:error, updated_at=now()"
        params = {"id": job_id, "status": status, "error": error}
        if attempts is not None:
            sets += ", attempts=:attempts"
            params["attempts"] = attempts
        await db.execute(text(f"UPDATE background_jobs SET {sets} WHERE id=:id"), params)
        await db.commit()


async def _retry(job_id, attempts: int, error: str, backoff_seconds: int) -> None:
    async with async_session() as db:
        await db.execute(text("""
            UPDATE background_jobs
            SET status='pending', attempts=:attempts, last_error=:error,
                run_after = now() + (:backoff || ' seconds')::interval,
                locked_at=NULL, updated_at=now()
            WHERE id=:id
        """), {"id": job_id, "attempts": attempts, "error": error, "backoff": str(backoff_seconds)})
        await db.commit()


async def run_worker(stop_event: asyncio.Event | None = None) -> None:
    """Long-running worker loop. Import handlers before starting so the registry
    is populated. Drains all ready jobs, then sleeps POLL_INTERVAL_SECONDS."""
    import app.services.jobs.handlers  # noqa: F401 — populate _HANDLERS
    logger.info("background-job worker started (handlers: %s)", ", ".join(sorted(_HANDLERS)))
    while not (stop_event and stop_event.is_set()):
        try:
            worked = await _run_one()
            if not worked:
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("background-job worker loop error (continuing)")
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
