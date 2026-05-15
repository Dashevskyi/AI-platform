"""
Admin endpoints for tenant usage statistics.
"""
import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, cast, Date
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.tenant import Tenant
from app.models.llm_request_log import LLMRequestLog
from app.schemas.stats import TenantStatsResponse, StatsSummary, DailyModelStats
from app.api.deps import require_role, require_tenant_access

router = APIRouter(
    prefix="/api/admin/tenants/{tenant_id}/stats",
    tags=["admin-stats"],
    dependencies=[Depends(require_role("superadmin", "tenant_admin")), Depends(require_tenant_access)],
)


async def _verify_tenant(tenant_id: uuid.UUID, db: AsyncSession):
    result = await db.execute(
        select(Tenant).where(Tenant.id == tenant_id, Tenant.deleted_at.is_(None))
    )
    if not result.scalars().first():
        raise HTTPException(status_code=404, detail="Tenant not found.")


@router.get("/", response_model=TenantStatsResponse)
async def get_stats(
    tenant_id: uuid.UUID,
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    await _verify_tenant(tenant_id, db)

    if not date_from:
        date_from = date.today() - timedelta(days=30)
    if not date_to:
        date_to = date.today()

    base = select(LLMRequestLog).where(
        LLMRequestLog.tenant_id == tenant_id,
        cast(LLMRequestLog.created_at, Date) >= date_from,
        cast(LLMRequestLog.created_at, Date) <= date_to,
        LLMRequestLog.status == "success",
    )

    # Summary totals
    summary_q = select(
        func.coalesce(func.sum(LLMRequestLog.total_tokens), 0).label("total_tokens"),
        func.coalesce(func.sum(LLMRequestLog.prompt_tokens), 0).label("prompt_tokens"),
        func.coalesce(func.sum(LLMRequestLog.completion_tokens), 0).label("completion_tokens"),
        func.coalesce(func.sum(LLMRequestLog.estimated_cost), 0).label("estimated_cost"),
        func.count().label("request_count"),
    ).where(
        LLMRequestLog.tenant_id == tenant_id,
        cast(LLMRequestLog.created_at, Date) >= date_from,
        cast(LLMRequestLog.created_at, Date) <= date_to,
        LLMRequestLog.status == "success",
    )

    summary_row = (await db.execute(summary_q)).one()
    summary = StatsSummary(
        total_tokens=summary_row.total_tokens,
        prompt_tokens=summary_row.prompt_tokens,
        completion_tokens=summary_row.completion_tokens,
        estimated_cost=float(summary_row.estimated_cost),
        request_count=summary_row.request_count,
    )

    # Daily breakdown by model
    daily_q = (
        select(
            cast(LLMRequestLog.created_at, Date).label("day"),
            LLMRequestLog.model_name,
            func.coalesce(func.sum(LLMRequestLog.total_tokens), 0).label("total_tokens"),
            func.coalesce(func.sum(LLMRequestLog.prompt_tokens), 0).label("prompt_tokens"),
            func.coalesce(func.sum(LLMRequestLog.completion_tokens), 0).label("completion_tokens"),
            func.coalesce(func.sum(LLMRequestLog.estimated_cost), 0).label("estimated_cost"),
            func.count().label("request_count"),
        )
        .where(
            LLMRequestLog.tenant_id == tenant_id,
            cast(LLMRequestLog.created_at, Date) >= date_from,
            cast(LLMRequestLog.created_at, Date) <= date_to,
            LLMRequestLog.status == "success",
        )
        .group_by("day", LLMRequestLog.model_name)
        .order_by("day")
    )

    daily_rows = (await db.execute(daily_q)).all()
    daily = [
        DailyModelStats(
            date=row.day,
            model_name=row.model_name,
            total_tokens=row.total_tokens,
            prompt_tokens=row.prompt_tokens,
            completion_tokens=row.completion_tokens,
            estimated_cost=float(row.estimated_cost),
            request_count=row.request_count,
        )
        for row in daily_rows
    ]

    return TenantStatsResponse(summary=summary, daily=daily)
