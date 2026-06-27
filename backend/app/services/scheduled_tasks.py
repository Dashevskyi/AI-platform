"""Periodic maintenance: scheduled routing-feedback jobs for all tenants."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select, text

from app.core.config import settings
from app.core.database import async_session
from app.models.tenant import Tenant
from app.services.jobs.queue import enqueue

logger = logging.getLogger(__name__)

_last_routing_feedback_at: datetime | None = None


async def _has_pending_job(db, tenant_id: str, job_type: str) -> bool:
    row = (await db.execute(text(
        "SELECT 1 FROM background_jobs"
        " WHERE tenant_id = CAST(:t AS uuid) AND job_type = :jt"
        " AND status IN ('pending', 'running') LIMIT 1"
    ), {"t": tenant_id, "jt": job_type})).first()
    return row is not None


async def enqueue_routing_feedback_for_all_tenants(*, days: int = 14, limit: int = 40) -> int:
    """Queue routing_feedback job per active tenant (skip if already pending)."""
    queued = 0
    async with async_session() as db:
        tenant_ids = list(
            (await db.execute(
                select(Tenant.id).where(Tenant.deleted_at.is_(None), Tenant.is_active.is_(True))
            )).scalars().all()
        )
        for tid in tenant_ids:
            tid_s = str(tid)
            if await _has_pending_job(db, tid_s, "routing_feedback"):
                continue
            await enqueue(
                db,
                "routing_feedback",
                {"tenant_id": tid_s, "days": days, "limit": limit, "dry_run": False},
                tenant_id=tid,
            )
            queued += 1
        await db.commit()
    logger.info("routing_feedback scheduler: queued %d jobs for %d tenants", queued, len(tenant_ids))
    return queued


async def scheduled_worker(stop_event: asyncio.Event | None = None) -> None:
    """Hourly tick — enqueue routing feedback when interval elapsed."""
    global _last_routing_feedback_at
    logger.info(
        "scheduled worker started (routing_feedback=%s, interval=%dh)",
        settings.ROUTING_FEEDBACK_SCHEDULER_ENABLED,
        settings.ROUTING_FEEDBACK_INTERVAL_HOURS,
    )
    while not (stop_event and stop_event.is_set()):
        try:
            if settings.ROUTING_FEEDBACK_SCHEDULER_ENABLED:
                now = datetime.now(timezone.utc)
                interval = max(1, settings.ROUTING_FEEDBACK_INTERVAL_HOURS) * 3600
                due = (
                    _last_routing_feedback_at is None
                    or (now - _last_routing_feedback_at).total_seconds() >= interval
                )
                if due:
                    await enqueue_routing_feedback_for_all_tenants(
                        days=settings.ROUTING_FEEDBACK_DAYS,
                        limit=settings.ROUTING_FEEDBACK_LIMIT,
                    )
                    _last_routing_feedback_at = now
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("scheduled worker tick failed")
        await asyncio.sleep(3600)
    logger.info("scheduled worker stopped")
