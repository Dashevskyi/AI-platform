from fastapi import APIRouter, Depends, Query

from app.api.deps import require_role
from app.services.gpu_metrics import fetch_history, fetch_live_snapshot

router = APIRouter(
    prefix="/api/admin/gpu",
    tags=["admin-gpu"],
    dependencies=[Depends(require_role("superadmin"))],
)


@router.get("/stats")
async def gpu_stats():
    """Live GPU + vLLM snapshot. Cached ~2.5s server-side."""
    return await fetch_live_snapshot()


@router.get("/history")
async def gpu_history(
    range: str = Query("1h", pattern="^(15m|1h|6h|24h|7d)$"),
):
    """Historical GPU+vLLM timeseries, downsampled to ~200 points."""
    seconds = {
        "15m": 15 * 60,
        "1h": 60 * 60,
        "6h": 6 * 60 * 60,
        "24h": 24 * 60 * 60,
        "7d": 7 * 24 * 60 * 60,
    }[range]
    return {"range": range, "points": await fetch_history(seconds)}
