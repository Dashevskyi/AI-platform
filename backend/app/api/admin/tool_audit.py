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


async def _ensure_audit_clone(db: AsyncSession, tenant_id: str, assistant_id: str) -> str:
    """Throwaway clone of the assistant with memory + cross-chat recall OFF, so
    audit cases are ISOLATED — a fact found in one case (e.g. switch_id) isn't
    recalled in the next, which would wrongly let the model skip a lookup tool.
    Must be is_active (resolver requires it); '__'-prefixed → hidden from the UI
    list. Refreshed each run to track the real assistant's config."""
    import json as _json
    src = (await db.execute(select(Assistant).where(Assistant.id == uuid.UUID(assistant_id)))).scalar_one_or_none()
    if not src:
        raise HTTPException(404, "Ассистент не найден")
    ov = dict(src.overrides or {})
    ov["memory_enabled"] = False
    ov["recall_cross_chat_enabled"] = False
    name = f"__audit__{assistant_id[:8]}"
    tl = _json.dumps(src.allowed_tool_ids) if src.allowed_tool_ids is not None else None
    existing = (await db.execute(select(Assistant).where(
        Assistant.tenant_id == uuid.UUID(tenant_id), Assistant.name == name))).scalar_one_or_none()
    if existing:
        await db.execute(text(
            "UPDATE assistants SET overrides=CAST(:ov AS jsonb), allowed_tool_ids=CAST(:tl AS jsonb),"
            " is_active=true WHERE id=:id"), {"ov": _json.dumps(ov), "tl": tl, "id": str(existing.id)})
        cid = str(existing.id)
    else:
        cid = str(uuid.uuid4())
        await db.execute(text(
            "INSERT INTO assistants (id,tenant_id,name,overrides,allowed_tool_ids,is_active,is_default,created_at)"
            " VALUES (:id,:t,:n,CAST(:ov AS jsonb),CAST(:tl AS jsonb),true,false,now())"),
            {"id": cid, "t": tenant_id, "n": name, "ov": _json.dumps(ov), "tl": tl})
    await db.commit()
    return cid


@router.post("/cases/{case_id}/run", dependencies=[Depends(require_tenant_access)])
async def run_case(tenant_id: str, assistant_id: str, case_id: str, repeats: int = 1,
                   db: AsyncSession = Depends(get_db)) -> dict:
    """Actually run ONE case through the LLM (repeats N) and cache the verdict."""
    c = (await db.execute(select(AssistantAuditCase).where(AssistantAuditCase.id == uuid.UUID(case_id)))).scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Кейс не найден")
    repeats = max(1, min(int(repeats), 5))
    clone_id = await _ensure_audit_clone(db, tenant_id, assistant_id)  # isolated (memory off)
    raw, prefix, kh = generate_api_key()
    kid = uuid.uuid4()
    await db.execute(text(
        "INSERT INTO tenant_api_keys (id,tenant_id,name,key_prefix,key_hash,assistant_id,actor_trusted,is_active,created_at)"
        " VALUES (:id,:t,'__audit_run__',:p,:h,:a,true,true,now())"),
        {"id": str(kid), "t": tenant_id, "p": prefix, "h": kh, "a": clone_id})
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
            exp_list = c.expected_tools or ["(NO_TOOL)"]
            called = set(lr.get("called") or [])
            # all acceptable tool names across every expected step (incl a|b variants)
            all_expected = {v for e in exp_list for v in str(e).split("|")}
            wrong = called - all_expected  # tools called that weren't wanted
            for exp in exp_list:
                # blame ONLY the steps that were actually MISSED (not satisfied),
                # so a multi-step case doesn't fault a tool it did call.
                if set(str(exp).split("|")) & called:
                    continue
                fail_by_tool[exp] += 1
                wrong_called.setdefault(exp, Counter()).update(wrong or ["(ничего)"])
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


# ============================================================
# Auto-tuning: read-only analysis → staged recommendations → Apply
# ============================================================
from app.models.tenant_tool import TenantTool  # noqa: E402
from app.models.assistant_tune import AssistantTuneRecommendation  # noqa: E402
from app.services import tuner as _tuner  # noqa: E402


async def _trace_for_chat(db: AsyncSession, cid: str) -> dict:
    """Rich per-case trace for diagnosis: offered tools, calls + args, tool
    results/errors, final answer."""
    row = (await db.execute(text(
        "SELECT debug, raw_request, raw_response, model_name FROM llm_request_logs"
        " WHERE chat_id=:c ORDER BY created_at DESC LIMIT 1"), {"c": cid})).mappings().first()
    dbg = (row or {}).get("debug") or {}
    tcs = [tc for tc in (dbg.get("tool_calls") or []) if isinstance(tc, dict) and tc.get("name")]
    called = {tc["name"] for tc in tcs}
    if (row or {}).get("model_name") == "tier0":
        t0 = (dbg.get("tier0") or {}).get("tool")
        if t0:
            called.add(t0)
    call_args = {tc["name"]: tc.get("args_preview") for tc in tcs}
    tool_ok = {tc["name"]: tc.get("ok") for tc in tcs}
    offered = [(t.get("function", {}).get("name") or t.get("name"))
               for t in (dbg.get("tools_payload") or []) if isinstance(t, dict)]
    # tool results: tool-role messages in raw_request, mapped by call order
    rq = (row or {}).get("raw_request") or {}
    names_order = [tc["name"] for tc in tcs]
    tool_results: dict = {}
    i = 0
    for m in (rq.get("messages") or []):
        if isinstance(m, dict) and m.get("role") == "tool":
            nm = names_order[i] if i < len(names_order) else f"tool{i}"
            tool_results[nm] = str(m.get("content") or "")[:400]
            i += 1
    rp = (row or {}).get("raw_response") or {}
    final = ((rp.get("choices") or [{}])[0].get("message", {}) or {}).get("content")
    return {"called": called, "call_args": call_args, "tool_ok": tool_ok,
            "tools_offered": offered, "tool_results": tool_results, "final_content": final}


def _classify_failure(expected: list[str], trace: dict) -> str:
    called = set(trace.get("called") or [])
    tool_ok = trace.get("tool_ok") or {}
    biz = called - META_TOOLS
    if not expected:
        return "pass" if not biz else "wrong_tool"
    satisfied = all((set(e.split("|")) & called) for e in expected)
    if not satisfied:
        return "no_tool_call" if not biz else "wrong_tool"
    for e in expected:
        for nm in (set(e.split("|")) & called):
            if tool_ok.get(nm) is False:
                return "tool_error"
    return "pass"


def _target_tool(expected: list[str], trace: dict, failure: str) -> str | None:
    """Which tool's config to diagnose for this failure."""
    if failure == "tool_error":
        for e in expected:
            for nm in (set(e.split("|")) & set(trace.get("called") or [])):
                if (trace.get("tool_ok") or {}).get(nm) is False:
                    return nm
    # no_tool_call / wrong_tool → the tool it SHOULD have called (first expected variant)
    if expected:
        return expected[0].split("|")[0]
    return None


async def _load_diagnoser(db: AsyncSession):
    """Heavy model used for diagnosis (DeepSeek V4-Flash by default)."""
    from app.providers.factory import get_provider
    from app.core.security import decrypt_value
    rec = (await db.execute(text(
        "SELECT provider_type, base_url, api_key_enc, model_id FROM llm_models"
        " WHERE model_id='deepseek-v4-flash' AND is_active=true LIMIT 1"))).mappings().first()
    if not rec:
        return None, None
    key = decrypt_value(rec["api_key_enc"]) if rec["api_key_enc"] else None
    return get_provider(rec["provider_type"], rec["base_url"], key), rec["model_id"]


def _current_value(cfg: dict, change_type: str, value, param_name: str | None):
    fn = (cfg or {}).get("function", {}); xb = (cfg or {}).get("x_backend_config", {})
    if change_type == "description":
        return fn.get("description")
    if change_type == "param_description":
        p = (value or {}).get("param") if isinstance(value, dict) else param_name
        return (((fn.get("parameters") or {}).get("properties") or {}).get(p) or {}).get("description")
    if change_type == "arg_format":
        path = (value or {}).get("path") if isinstance(value, dict) else param_name
        return (xb.get("arg_formats") or {}).get(str(path))
    if change_type == "tier0":
        return xb.get("tier0_template")
    if change_type in ("usage_example", "capability_tag"):
        return xb.get("usage_examples" if change_type == "usage_example" else "capability_tags")
    return None


def _rec_dict(r: AssistantTuneRecommendation) -> dict:
    return {
        "id": str(r.id), "scope": r.scope, "tool_name": r.tool_name,
        "change_type": r.change_type, "json_path": r.json_path, "param_name": r.param_name,
        "current_value": r.current_value, "proposed_value": r.proposed_value,
        "rationale": r.rationale, "deterministic": r.deterministic,
        "failing_case_ids": r.failing_case_ids or [], "status": r.status,
    }


@router.post("/tune", dependencies=[Depends(require_tenant_access)])
async def tune(tenant_id: str, assistant_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    """READ-ONLY: run active cases on the light model, classify failures, ask the
    heavy model for config-change proposals, and STAGE them as recommendations.
    Touches NO live config — apply happens only on explicit /apply."""
    cases = (await db.execute(select(AssistantAuditCase).where(
        AssistantAuditCase.assistant_id == uuid.UUID(assistant_id),
        AssistantAuditCase.active == True)  # noqa: E712
        .order_by(AssistantAuditCase.order_index, AssistantAuditCase.created_at))).scalars().all()
    if not cases:
        raise HTTPException(400, "Нет активных кейсов для прогона")

    clone_id = await _ensure_audit_clone(db, tenant_id, assistant_id)
    raw, prefix, kh = generate_api_key()
    kid = uuid.uuid4()
    await db.execute(text(
        "INSERT INTO tenant_api_keys (id,tenant_id,name,key_prefix,key_hash,assistant_id,actor_trusted,is_active,created_at)"
        " VALUES (:id,:t,'__audit_tune__',:p,:h,:a,true,true,now())"),
        {"id": str(kid), "t": tenant_id, "p": prefix, "h": kh, "a": clone_id})
    await db.commit()

    run_id = uuid.uuid4()
    failures: list[tuple] = []   # (case, trace, failure_class)
    chat_ids: list = []
    ran = 0
    try:
        async with httpx.AsyncClient(timeout=120) as cl:
            for c in cases:
                actor = c.actor or {"role": "operator", "external_id": "audit"}
                try:
                    ch = await cl.post(f"{API_BASE}/api/tenants/{tenant_id}/chats/",
                                       headers={"X-API-Key": raw}, json={})
                    cid = ch.json()["id"]; chat_ids.append(cid)
                    await cl.post(f"{API_BASE}/api/tenants/{tenant_id}/chats/{cid}/messages",
                                  headers={"X-API-Key": raw}, json={"content": c.question, "actor": actor})
                    trace = await _trace_for_chat(db, cid)
                except Exception as e:
                    trace = {"called": set(), "error": str(e)[:120]}
                ran += 1
                fc = _classify_failure(c.expected_tools or [], trace)
                trace["failure_class"] = fc
                if fc != "pass":
                    failures.append((c, trace, fc))
    finally:
        await _cleanup_chats(db, chat_ids, str(kid))

    # Wipe previous PENDING recs for a fresh list (applied/dismissed are kept).
    await db.execute(text(
        "DELETE FROM assistant_tune_recommendations WHERE assistant_id=:a AND status='pending'"),
        {"a": assistant_id})
    await db.commit()

    provider, dmodel = await _load_diagnoser(db)
    # tool name -> (id, config_json)
    tool_rows = (await db.execute(select(TenantTool).where(
        TenantTool.tenant_id == uuid.UUID(tenant_id),
        TenantTool.deleted_at.is_(None)))).scalars().all()
    by_name = {t.name: t for t in tool_rows}

    staged: dict = {}   # dedup key -> rec payload
    diagnosed = 0
    if provider:
        for c, trace, fc in failures:
            tname = _target_tool(c.expected_tools or [], trace, fc)
            tool = by_name.get(tname) if tname else None
            cfg = tool.config_json if tool else None
            try:
                proposals = await _tuner.diagnose(provider, dmodel, _case_dict(c), trace, cfg, tname or "?")
            except Exception:
                proposals = []
            diagnosed += 1
            for p in proposals:
                ct = p["change_type"]
                if ct == "ontology":
                    key = ("assistant", ct, json_dumps_safe(p["value"]))
                    rec = staged.get(key) or {
                        "scope": "assistant", "tool_id": None, "tool_name": None,
                        "change_type": ct, "json_path": "overrides.ontology_prompt", "param_name": None,
                        "current_value": None, "proposed_value": p["value"],
                        "rationale": p["rationale"], "deterministic": False, "cases": set(),
                    }
                else:
                    if not tool:
                        continue
                    pname = (p["value"] or {}).get("param") if (ct == "param_description" and isinstance(p["value"], dict)) else \
                            ((p["value"] or {}).get("path") if (ct == "arg_format" and isinstance(p["value"], dict)) else None)
                    key = (str(tool.id), ct, pname, json_dumps_safe(p["value"]))
                    rec = staged.get(key) or {
                        "scope": "tool", "tool_id": tool.id, "tool_name": tool.name,
                        "change_type": ct, "json_path": f"{tool.name}.{ct}" + (f".{pname}" if pname else ""),
                        "param_name": pname,
                        "current_value": _current_value(cfg or {}, ct, p["value"], pname),
                        "proposed_value": p["value"], "rationale": p["rationale"],
                        "deterministic": p["deterministic"], "cases": set(),
                    }
                rec["cases"].add(str(c.id))
                staged[key] = rec

    for rec in staged.values():
        db.add(AssistantTuneRecommendation(
            tenant_id=uuid.UUID(tenant_id), assistant_id=uuid.UUID(assistant_id), run_id=run_id,
            scope=rec["scope"], tool_id=rec["tool_id"], tool_name=rec["tool_name"],
            change_type=rec["change_type"], json_path=rec["json_path"], param_name=rec["param_name"],
            current_value=rec["current_value"], proposed_value=rec["proposed_value"],
            rationale=rec["rationale"], deterministic=rec["deterministic"],
            failing_case_ids=sorted(rec["cases"]), status="pending"))
    await db.commit()
    return {
        "ran": ran, "failed": len(failures), "diagnosed": diagnosed,
        "recommendations": len(staged), "diagnoser": dmodel,
        "failures_by_class": _count_by_class(failures),
    }


def json_dumps_safe(v) -> str:
    import json as _j
    try:
        return _j.dumps(v, ensure_ascii=False, sort_keys=True)
    except Exception:
        return str(v)


def _count_by_class(failures: list) -> dict:
    from collections import Counter
    c = Counter(fc for _, _, fc in failures)
    return dict(c)


@router.get("/recommendations", dependencies=[Depends(require_tenant_access)])
async def list_recommendations(tenant_id: str, assistant_id: str, status: str = "pending",
                               db: AsyncSession = Depends(get_db)) -> dict:
    q = select(AssistantTuneRecommendation).where(
        AssistantTuneRecommendation.assistant_id == uuid.UUID(assistant_id))
    if status != "all":
        q = q.where(AssistantTuneRecommendation.status == status)
    rows = (await db.execute(q.order_by(
        AssistantTuneRecommendation.deterministic.desc(),
        AssistantTuneRecommendation.created_at.desc()))).scalars().all()
    return {"recommendations": [_rec_dict(r) for r in rows]}


@router.post("/recommendations/{rec_id}/apply", dependencies=[Depends(require_tenant_access)])
async def apply_recommendation(tenant_id: str, assistant_id: str, rec_id: str,
                               db: AsyncSession = Depends(get_db)) -> dict:
    """The ONLY write path. Applies one recommendation to the live config."""
    from sqlalchemy.orm.attributes import flag_modified
    from datetime import datetime, timezone
    r = (await db.execute(select(AssistantTuneRecommendation).where(
        AssistantTuneRecommendation.id == uuid.UUID(rec_id)))).scalar_one_or_none()
    if not r:
        raise HTTPException(404, "Рекомендация не найдена")
    if r.status == "applied":
        return {"ok": True, "already": True}

    reembed = False
    if r.scope == "assistant" and r.change_type == "ontology":
        a = (await db.execute(select(Assistant).where(Assistant.id == uuid.UUID(assistant_id)))).scalar_one_or_none()
        if not a:
            raise HTTPException(404, "Ассистент не найден")
        ov = dict(a.overrides or {})
        prev = ov.get("ontology_prompt") or ""
        add = r.proposed_value if isinstance(r.proposed_value, str) else str(r.proposed_value)
        ov["ontology_prompt"] = (prev + "\n" + add).strip() if prev else add
        a.overrides = ov
        flag_modified(a, "overrides")
    else:
        tool = (await db.execute(select(TenantTool).where(TenantTool.id == r.tool_id))).scalar_one_or_none()
        if not tool:
            raise HTTPException(404, "Тул не найден")
        tool.config_json = _tuner.apply_to_tool_config(
            tool.config_json or {}, r.change_type, r.proposed_value, r.param_name)
        flag_modified(tool, "config_json")
        reembed = r.change_type in ("description", "param_description", "usage_example", "capability_tag")

    r.status = "applied"
    r.applied_at = datetime.now(timezone.utc)
    await db.commit()
    if reembed and r.tool_id:
        try:
            from app.services.tools.embedder import embed_tool
            await embed_tool(r.tool_id)
        except Exception:
            logger.warning("tune apply: re-embed failed for tool %s", r.tool_id)
    return {"ok": True, "scope": r.scope, "reembedded": reembed}


@router.post("/recommendations/{rec_id}/dismiss", dependencies=[Depends(require_tenant_access)])
async def dismiss_recommendation(tenant_id: str, assistant_id: str, rec_id: str,
                                 db: AsyncSession = Depends(get_db)) -> dict:
    await db.execute(text(
        "UPDATE assistant_tune_recommendations SET status='dismissed' WHERE id=:i"),
        {"i": rec_id})
    await db.commit()
    return {"ok": True}
