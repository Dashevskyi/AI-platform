"""Tool-routing audit (preview) for an assistant — pre-release sanity check.

Deterministic, no LLM: for each question it shows WHAT the pipeline would surface
to the model (semantic catalog scoped to the assistant's tools, with scores/rank)
and the Tier-0 verdict, then flags the cases our routing bugs live in:
  • the expected tool isn't in the catalog at all (weak description/embedding),
  • Tier-0 would short-circuit to a different (out-of-scope) tool,
  • the expected tool is surfaced but at a low rank (close competitors).

This is the engine behind the audit UI. Full LLM-run + recommendation agent
(audit_recommendations.py) plug in on top.
"""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.api.deps import require_tenant_access
from app.models.assistant import Assistant
from app.models.tenant_shell_config import TenantShellConfig
from app.services.tools.embedder import search_tools
from app.services.llm.tier0_router import explain_tier0

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin/tenants/{tenant_id}/assistants/{assistant_id}/tool-audit",
    tags=["tool-audit"],
)

TOP_K = 10


class AuditCase(BaseModel):
    question: str
    expect_tool: str | None = None   # single name, or "a|b" any-of


class AuditRequest(BaseModel):
    cases: list[AuditCase]


def _verdict(expect: str | None, surfaced: list[dict], tier0: dict) -> dict:
    """Classify one case from the deterministic signals."""
    dec = tier0.get("decision") or {}
    t0_fires = bool(dec.get("fired"))
    t0_tool = dec.get("tool")
    if not expect:
        return {"level": "info", "msg": "ожидаемый тул не задан — только обзор каталога"}
    wanted = set(expect.split("|"))
    names = [s["name"] for s in surfaced]
    rank = next((i + 1 for i, n in enumerate(names) if n in wanted), None)
    if t0_fires and t0_tool and t0_tool not in wanted:
        return {"level": "error", "msg": f"Tier-0 уведёт в `{t0_tool}` (мимо ожидаемого) — закоротит каталог"}
    if rank is None:
        return {"level": "error", "msg": f"Ожидаемый `{expect}` НЕ в каталоге (top-{TOP_K}) → улучшить описание/теги"}
    if rank > 3:
        return {"level": "warn", "msg": f"Ожидаемый `{expect}` показан, но низкий ранг #{rank} (близкие конкуренты)"}
    return {"level": "ok", "msg": f"OK — `{expect}` на #{rank}"}


@router.post("/preview", dependencies=[Depends(require_tenant_access)])
async def audit_preview(
    tenant_id: str, assistant_id: str, body: AuditRequest, db: AsyncSession = Depends(get_db),
) -> dict:
    """Per-question routing preview + a flagged-issue summary. Deterministic."""
    a = (await db.execute(select(Assistant).where(
        Assistant.id == uuid.UUID(assistant_id), Assistant.tenant_id == uuid.UUID(tenant_id)))).scalar_one_or_none()
    if not a:
        raise HTTPException(404, "Ассистент не найден")
    shell = (await db.execute(select(TenantShellConfig).where(
        TenantShellConfig.tenant_id == uuid.UUID(tenant_id)))).scalar_one_or_none()

    ov = a.overrides or {}
    embedding_model = ov.get("embedding_model_name") or getattr(shell, "embedding_model_name", None)
    min_score = float(ov.get("tier0_min_tool_score") or getattr(shell, "tier0_min_tool_score", None) or 0.80)
    max_gap = float(ov.get("tier0_max_score_gap") or getattr(shell, "tier0_max_score_gap", None) or 0.15)
    tier0_on = ov.get("tier0_enabled", getattr(shell, "tier0_enabled", False))
    cand = [uuid.UUID(x) for x in (a.allowed_tool_ids or [])] or None  # None = all tenant tools

    results = []
    summary: dict[str, int] = {"ok": 0, "warn": 0, "error": 0, "info": 0}
    for case in body.cases:
        q = case.question.strip()
        if not q:
            continue
        try:
            tools = await search_tools(tenant_id=tenant_id, query=q, db=db,
                                       embedding_model=embedding_model, candidate_ids=cand, top_k=TOP_K)
            surfaced = [{"name": t.name, "score": round(float(getattr(t, "_semantic_score", 0.0) or 0.0), 3)}
                        for t in tools]
        except Exception as e:
            surfaced = []
            logger.warning("audit search_tools failed: %s", e)
        try:
            t0 = await explain_tier0(tenant_id=tenant_id, user_query=q, db=db,
                                     embedding_model=embedding_model, min_tool_score=min_score,
                                     max_score_gap=max_gap, focus_tool=(case.expect_tool or None))
        except Exception as e:
            t0 = {"error": str(e)[:120]}
        # tier0 only matters at runtime if enabled for this assistant
        t0_eff = dict(t0); t0_eff["enabled"] = bool(tier0_on)
        v = _verdict(case.expect_tool, surfaced, t0_eff if tier0_on else {})
        summary[v["level"]] = summary.get(v["level"], 0) + 1
        results.append({
            "question": q, "expect_tool": case.expect_tool,
            "surfaced": surfaced, "tier0": t0_eff, "verdict": v,
        })
    return {
        "assistant": {"id": str(a.id), "name": a.name, "tool_count": len(a.allowed_tool_ids or [])},
        "tier0_enabled": bool(tier0_on),
        "summary": summary,
        "results": results,
    }
