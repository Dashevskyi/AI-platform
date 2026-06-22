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
from sqlalchemy import select, text
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


# ============================ saved audit suite ============================
import httpx
from sqlalchemy import delete as sa_delete
from app.core.security import generate_api_key
from app.models.assistant_audit import AssistantAuditCase, AssistantAuditRun

API_BASE = "http://127.0.0.1:8000"
META_TOOLS = {
    "search_kb", "recall_memory", "recall_chat", "describe_tool", "plan",
    "plan_update", "memory_save", "get_artifact", "find_artifacts", "get_message",
}
_CHAT_CHILDREN = ("llm_request_logs", "message_attachments", "artifacts",
                  "memory_entries", "pending_tool_actions", "messages")


class CaseIn(BaseModel):
    question: str
    expected_tools: list[str] | None = None
    actor: dict | None = None
    notes: str | None = None
    active: bool = True
    order_index: int = 0


class CasePatch(BaseModel):
    question: str | None = None
    expected_tools: list[str] | None = None
    actor: dict | None = None
    notes: str | None = None
    active: bool | None = None
    order_index: int | None = None


def _case_dict(c: AssistantAuditCase) -> dict:
    return {
        "id": str(c.id), "active": c.active, "question": c.question,
        "expected_tools": c.expected_tools or [], "actor": c.actor,
        "notes": c.notes, "order_index": c.order_index, "last_result": c.last_result,
    }


def _passes(expected: list[str], called: set[str]) -> bool:
    """All expected tools present (each item may be 'a|b' any-of). Empty expected
    = conversational → pass iff no DATA (non-meta) tool was called."""
    if not expected:
        return not (called - META_TOOLS)
    for item in expected:
        if not (set(item.split("|")) & called):
            return False
    return True


@router.get("/cases", dependencies=[Depends(require_tenant_access)])
async def list_cases(tenant_id: str, assistant_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    rows = (await db.execute(select(AssistantAuditCase).where(
        AssistantAuditCase.assistant_id == uuid.UUID(assistant_id))
        .order_by(AssistantAuditCase.order_index, AssistantAuditCase.created_at))).scalars().all()
    return {"cases": [_case_dict(c) for c in rows]}


@router.post("/cases", dependencies=[Depends(require_tenant_access)])
async def create_case(tenant_id: str, assistant_id: str, body: CaseIn, db: AsyncSession = Depends(get_db)) -> dict:
    c = AssistantAuditCase(
        tenant_id=uuid.UUID(tenant_id), assistant_id=uuid.UUID(assistant_id),
        question=body.question.strip(), expected_tools=body.expected_tools or [],
        actor=body.actor, notes=body.notes, active=body.active, order_index=body.order_index)
    db.add(c); await db.commit(); await db.refresh(c)
    return _case_dict(c)


@router.patch("/cases/{case_id}", dependencies=[Depends(require_tenant_access)])
async def update_case(tenant_id: str, assistant_id: str, case_id: str, body: CasePatch,
                      db: AsyncSession = Depends(get_db)) -> dict:
    c = (await db.execute(select(AssistantAuditCase).where(AssistantAuditCase.id == uuid.UUID(case_id)))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Кейс не найден")
    for f in ("question", "expected_tools", "actor", "notes", "active", "order_index"):
        v = getattr(body, f)
        if v is not None:
            setattr(c, f, v)
    await db.commit(); await db.refresh(c)
    return _case_dict(c)


@router.delete("/cases/{case_id}", dependencies=[Depends(require_tenant_access)])
async def delete_case(tenant_id: str, assistant_id: str, case_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    await db.execute(sa_delete(AssistantAuditCase).where(AssistantAuditCase.id == uuid.UUID(case_id)))
    await db.commit()
    return {"ok": True}


async def _called_tools_for_chat(db: AsyncSession, cid: str) -> tuple[set, dict]:
    row = (await db.execute(text(
        "SELECT debug, model_name, latency_ms, total_tokens FROM llm_request_logs"
        " WHERE chat_id=:c ORDER BY created_at DESC LIMIT 1"), {"c": cid})).mappings().first()
    dbg = (row or {}).get("debug") or {}
    names = {tc.get("name") for tc in (dbg.get("tool_calls") or []) if isinstance(tc, dict) and tc.get("name")}
    if (row or {}).get("model_name") == "tier0":
        t0 = (dbg.get("tier0") or {}).get("tool")
        if t0:
            names.add(t0)
    meta = {
        "model_name": (row or {}).get("model_name"),
        "latency_ms": (row or {}).get("latency_ms"),
        "tokens": (row or {}).get("total_tokens"),
        "tools_payload": [(t.get("function", {}).get("name") or t.get("name"))
                          for t in (dbg.get("tools_payload") or []) if isinstance(t, dict)],
        "tool_calls": [tc.get("name") for tc in (dbg.get("tool_calls") or []) if isinstance(tc, dict)],
        "tier0": dbg.get("tier0"),
    }
    return names, meta


async def _cleanup_chats(db: AsyncSession, chat_ids: list, key_id) -> None:
    if chat_ids:
        for child in _CHAT_CHILDREN:
            await db.execute(text(f"DELETE FROM {child} WHERE chat_id = ANY(:c)"), {"c": chat_ids})
        await db.execute(text("DELETE FROM chats WHERE id = ANY(:c)"), {"c": chat_ids})
    if key_id:
        await db.execute(text("DELETE FROM llm_request_logs WHERE api_key_id=:k"), {"k": key_id})
        await db.execute(text("DELETE FROM tenant_api_keys WHERE id=:k"), {"k": key_id})
    await db.commit()


@router.post("/cases/{case_id}/run", dependencies=[Depends(require_tenant_access)])
async def run_case(tenant_id: str, assistant_id: str, case_id: str, repeats: int = 1,
                   db: AsyncSession = Depends(get_db)) -> dict:
    """Actually run ONE case through the LLM (repeats N) and cache the verdict."""
    c = (await db.execute(select(AssistantAuditCase).where(AssistantAuditCase.id == uuid.UUID(case_id)))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Кейс не найден")
    repeats = max(1, min(int(repeats), 5))
    raw, prefix, kh = generate_api_key()
    kid = uuid.uuid4()
    await db.execute(text(
        "INSERT INTO tenant_api_keys (id,tenant_id,name,key_prefix,key_hash,assistant_id,actor_trusted,is_active,created_at)"
        " VALUES (:id,:t,'__audit_run__',:p,:h,:a,true,true,now())"),
        {"id": str(kid), "t": tenant_id, "p": prefix, "h": kh, "a": assistant_id})
    await db.commit()
    actor = c.actor or {"role": "operator", "external_id": "audit"}
    expected = c.expected_tools or []
    chat_ids, passes, last_meta, last_called = [], 0, None, []
    try:
        async with httpx.AsyncClient(timeout=120) as cl:
            for _ in range(repeats):
                try:
                    ch = await cl.post(f"{API_BASE}/api/tenants/{tenant_id}/chats/", headers={"X-API-Key": raw}, json={})
                    cid = ch.json()["id"]; chat_ids.append(cid)
                    await cl.post(f"{API_BASE}/api/tenants/{tenant_id}/chats/{cid}/messages",
                                  headers={"X-API-Key": raw}, json={"content": c.question, "actor": actor})
                    called, meta = await _called_tools_for_chat(db, cid)
                except Exception as e:
                    called, meta = set(), {"error": str(e)[:120]}
                if _passes(expected, called):
                    passes += 1
                last_meta = meta; last_called = sorted(called - META_TOOLS)
    finally:
        await _cleanup_chats(db, chat_ids, str(kid))
    result = {
        "passed": passes == repeats, "pass_rate": round(passes / repeats, 2), "repeats": repeats,
        "called": last_called, "debug": last_meta,
        "ts": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
    }
    c.last_result = result
    await db.commit()
    return result


@router.get("/cases/{case_id}/tool-log", dependencies=[Depends(require_tenant_access)])
async def case_tool_log(tenant_id: str, assistant_id: str, case_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    c = (await db.execute(select(AssistantAuditCase).where(AssistantAuditCase.id == uuid.UUID(case_id)))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Кейс не найден")
    lr = c.last_result or {}
    return {"called": lr.get("called"), "debug": lr.get("debug"), "ts": lr.get("ts")}


def _by_tool_summary(cases: list[AssistantAuditCase]) -> dict:
    """Group failing cases by intended tool → misses + share (the bottom summary)."""
    from collections import Counter
    fail_by_tool: Counter = Counter()
    wrong_called: dict[str, Counter] = {}
    total_fail = 0
    for c in cases:
        lr = c.last_result or {}
        if lr and not lr.get("passed"):
            total_fail += 1
            for exp in (c.expected_tools or ["(NO_TOOL)"]):
                fail_by_tool[exp] += 1
                wrong_called.setdefault(exp, Counter()).update(lr.get("called") or ["(ничего)"])
    out = []
    for tool, n in fail_by_tool.most_common():
        out.append({
            "tool": tool, "misses": n,
            "share": round(100 * n / max(total_fail, 1)),
            "called_instead": dict(wrong_called[tool].most_common(3)),
        })
    return {"total_failed": total_fail, "by_tool": out}


@router.post("/runs", dependencies=[Depends(require_tenant_access)])
async def snapshot_run(tenant_id: str, assistant_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    """Snapshot the current cases' cached results into a run (for the trend)."""
    cases = (await db.execute(select(AssistantAuditCase).where(
        AssistantAuditCase.assistant_id == uuid.UUID(assistant_id),
        AssistantAuditCase.active.is_(True)))).scalars().all()
    ran = [c for c in cases if c.last_result]
    passed = sum(1 for c in ran if (c.last_result or {}).get("passed"))
    summary = _by_tool_summary(ran)
    run = AssistantAuditRun(tenant_id=uuid.UUID(tenant_id), assistant_id=uuid.UUID(assistant_id),
                            total=len(ran), passed=passed, summary=summary)
    db.add(run); await db.commit(); await db.refresh(run)
    return {"id": str(run.id), "total": len(ran), "passed": passed, "summary": summary}


@router.get("/stats", dependencies=[Depends(require_tenant_access)])
async def audit_stats(tenant_id: str, assistant_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    cases = (await db.execute(select(AssistantAuditCase).where(
        AssistantAuditCase.assistant_id == uuid.UUID(assistant_id)))).scalars().all()
    active = [c for c in cases if c.active]
    ran = [c for c in active if c.last_result]
    passed = sum(1 for c in ran if (c.last_result or {}).get("passed"))
    runs = (await db.execute(select(AssistantAuditRun).where(
        AssistantAuditRun.assistant_id == uuid.UUID(assistant_id))
        .order_by(AssistantAuditRun.created_at.desc()).limit(15))).scalars().all()
    trend = [{"ts": r.created_at.isoformat() if r.created_at else None,
              "passed": r.passed, "total": r.total} for r in reversed(runs)]
    return {
        "active": len(active), "ran": len(ran), "passed": passed,
        "pass_pct": round(100 * passed / max(len(ran), 1)),
        "by_tool": _by_tool_summary(ran),
        "trend": trend,
    }


# ---------------- seed cases from real logs ----------------
import re as _re

_NOISE = _re.compile(r"^[\d\s()+\-.,:/]{3,}$")


def _is_noise(q: str) -> bool:
    s = (q or "").strip()
    if len(s) < 8 or len(s) > 200 or _NOISE.match(s):
        return True
    return sum(ch.isalpha() for ch in s) < max(4, len(s) // 3)


class SeedRequest(BaseModel):
    limit: int = 30


@router.post("/seed-from-logs", dependencies=[Depends(require_tenant_access)])
async def seed_from_logs(tenant_id: str, assistant_id: str, body: SeedRequest,
                         db: AsyncSession = Depends(get_db)) -> dict:
    """Harvest distinct real user questions from this tenant's chats → create
    INACTIVE cases with a candidate expected tool (top semantic match). Admin
    reviews/activates. Skips questions already present as cases."""
    a = (await db.execute(select(Assistant).where(Assistant.id == uuid.UUID(assistant_id)))).scalar_one_or_none()
    if not a:
        raise HTTPException(404, "Ассистент не найден")
    shell = (await db.execute(select(TenantShellConfig).where(
        TenantShellConfig.tenant_id == uuid.UUID(tenant_id)))).scalar_one_or_none()
    embedding_model = (a.overrides or {}).get("embedding_model_name") or getattr(shell, "embedding_model_name", None)
    cand = [uuid.UUID(x) for x in (a.allowed_tool_ids or [])] or None

    existing = {(c.question or "").strip().lower() for c in (await db.execute(select(AssistantAuditCase).where(
        AssistantAuditCase.assistant_id == uuid.UUID(assistant_id)))).scalars().all()}
    rows = (await db.execute(text(
        """SELECT DISTINCT ON (lower(trim(m.content))) m.content
           FROM messages m WHERE m.tenant_id=:t AND m.role='user'
           ORDER BY lower(trim(m.content)), m.created_at DESC LIMIT 400"""), {"t": tenant_id})).all()
    questions = []
    for (content,) in rows:
        q = " ".join((content or "").split())
        if not _is_noise(q) and q.lower() not in existing:
            questions.append(q)
        if len(questions) >= body.limit:
            break

    created = 0
    for i, q in enumerate(questions):
        try:
            res = await search_tools(tenant_id=tenant_id, query=q, db=db,
                                     embedding_model=embedding_model, candidate_ids=cand, top_k=1)
            cand_tool = res[0].name if res else None
        except Exception:
            cand_tool = None
        db.add(AssistantAuditCase(
            tenant_id=uuid.UUID(tenant_id), assistant_id=uuid.UUID(assistant_id),
            question=q, expected_tools=[cand_tool] if cand_tool else [], active=False,
            notes="seed: candidate tool, проверь", order_index=1000 + i))
        created += 1
    await db.commit()
    return {"created": created, "scanned": len(rows)}
