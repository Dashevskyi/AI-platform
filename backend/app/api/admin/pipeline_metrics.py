"""Pipeline observability endpoints."""
from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from app.api.deps import require_role

router = APIRouter(
    prefix="/api/admin/metrics",
    tags=["admin-metrics"],
    dependencies=[Depends(require_role("superadmin"))],
)


class RoutingFeedbackScheduleBody(BaseModel):
    days: int = 14
    limit: int = 40


@router.get("/pipeline")
async def pipeline_metrics_json() -> dict:
    from app.services.llm.pipeline_metrics import pipeline_metrics
    return pipeline_metrics.snapshot()


@router.get("/pipeline/prometheus", response_class=PlainTextResponse)
async def pipeline_metrics_prometheus() -> str:
    from app.services.llm.pipeline_metrics import pipeline_metrics
    return pipeline_metrics.prometheus_text()


@router.post("/routing-feedback/schedule-all")
async def schedule_routing_feedback_all(body: RoutingFeedbackScheduleBody | None = None) -> dict:
    """Queue routing_feedback job for every active tenant (skip if already pending)."""
    from app.services.scheduled_tasks import enqueue_routing_feedback_for_all_tenants

    opts = body or RoutingFeedbackScheduleBody()
    queued = await enqueue_routing_feedback_for_all_tenants(days=opts.days, limit=opts.limit)
    return {"queued": queued, "days": opts.days, "limit": opts.limit}
