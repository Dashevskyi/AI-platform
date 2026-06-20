"""
Admin CRUD for tenant tools.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.admin_user import AdminUser
from app.models.tenant import Tenant
from app.models.tenant_tool import TenantTool
from app.schemas.tool import ToolCreate, ToolUpdate, ToolResponse, ToolTestRequest, ToolTestResponse
from app.schemas.common import PaginatedResponse
from app.api.deps import require_role, require_tenant_access, require_permission
from app.services.tools.executor import execute_tool

router = APIRouter(
    prefix="/api/admin/tenants/{tenant_id}/tools",
    tags=["admin-tools"],
    dependencies=[Depends(require_role("superadmin", "tenant_admin")), Depends(require_tenant_access), Depends(require_permission("tools"))],
)


def _tool_to_response(t: TenantTool) -> ToolResponse:
    return ToolResponse(
        id=str(t.id),
        tenant_id=str(t.tenant_id),
        name=t.name,
        description=t.description,
        group=t.group,
        config_json=t.config_json,
        tool_type=t.tool_type,
        is_active=t.is_active,
        is_pinned=t.is_pinned,
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


async def _verify_tenant(tenant_id: uuid.UUID, db: AsyncSession) -> Tenant:
    result = await db.execute(
        select(Tenant).where(Tenant.id == tenant_id, Tenant.deleted_at.is_(None))
    )
    tenant = result.scalars().first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found.")
    return tenant


def _get_function_name(config_json: dict | None) -> str | None:
    if not isinstance(config_json, dict):
        return None
    function_cfg = config_json.get("function")
    if not isinstance(function_cfg, dict):
        return None
    name = function_cfg.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


async def _validate_tool_payload(
    tenant_id: uuid.UUID,
    config_json: dict | None,
    db: AsyncSession,
    *,
    exclude_tool_id: uuid.UUID | None = None,
):
    function_name = _get_function_name(config_json)
    if not function_name:
        raise HTTPException(status_code=422, detail="config_json.function.name обязателен")

    existing = (
        await db.execute(
            select(TenantTool).where(
                TenantTool.tenant_id == tenant_id,
                TenantTool.deleted_at.is_(None),
            )
        )
    ).scalars().all()
    for tool in existing:
        if exclude_tool_id and tool.id == exclude_tool_id:
            continue
        if _get_function_name(tool.config_json) == function_name:
            raise HTTPException(
                status_code=409,
                detail=f"Tool function.name '{function_name}' уже используется у этого tenant.",
            )


class ToolMetric(BaseModel):
    name: str
    calls: int
    errors: int
    success_rate: float
    avg_latency_ms: float | None


@router.get("/metrics", response_model=list[ToolMetric])
async def tool_metrics(
    tenant_id: uuid.UUID,
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Per-tool call counts / success rate / avg latency, aggregated from the
    tool_calls recorded in each request's debug trace (debug.tool_calls)."""
    await _verify_tenant(tenant_id, db)
    rows = (await db.execute(text("""
        SELECT tc->>'name' AS name,
               count(*) AS calls,
               count(*) FILTER (WHERE (tc->>'ok')::boolean IS NOT TRUE) AS errors,
               avg((tc->>'latency_ms')::numeric) AS avg_latency
        FROM llm_request_logs l,
             LATERAL jsonb_array_elements(
                 CASE WHEN jsonb_typeof(l.debug->'tool_calls') = 'array'
                      THEN l.debug->'tool_calls' ELSE '[]'::jsonb END
             ) tc
        WHERE l.tenant_id = :tid
          AND (CAST(:date_from AS timestamptz) IS NULL OR l.created_at >= CAST(:date_from AS timestamptz))
          AND (CAST(:date_to AS timestamptz) IS NULL OR l.created_at <= CAST(:date_to AS timestamptz))
        GROUP BY tc->>'name'
        ORDER BY calls DESC
    """), {"tid": tenant_id, "date_from": date_from, "date_to": date_to})).mappings().all()
    out = []
    for r in rows:
        calls = r["calls"] or 0
        errors = r["errors"] or 0
        out.append(ToolMetric(
            name=r["name"] or "(unknown)",
            calls=calls,
            errors=errors,
            success_rate=((calls - errors) / calls) if calls else 0.0,
            avg_latency_ms=float(r["avg_latency"]) if r["avg_latency"] is not None else None,
        ))
    return out


class ToolCallRecord(BaseModel):
    created_at: datetime
    chat_id: str | None
    message_id: str | None
    ok: bool
    args_preview: str | None
    output_chars: int | None
    latency_ms: int | None
    round: int | None


@router.get("/calls", response_model=list[ToolCallRecord])
async def tool_calls(
    tenant_id: uuid.UUID,
    name: str = Query(..., description="имя инструмента (function name)"),
    status: str | None = Query(None, description="success | error"),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Individual invocations of one tool (newest first) — the params it was
    called with, success/failure, output size, latency, and the chat it ran in.
    Drill-down behind the per-tool usage counters. The actual returned data is
    NOT stored; the UI offers a 'repeat call' that re-runs the tool live."""
    await _verify_tenant(tenant_id, db)
    ok_filter = ""
    if status == "success":
        ok_filter = "AND (tc->>'ok')::boolean IS TRUE"
    elif status == "error":
        ok_filter = "AND (tc->>'ok')::boolean IS NOT TRUE"
    rows = (await db.execute(text(f"""
        SELECT l.created_at, l.chat_id, l.message_id,
               (tc->>'ok')::boolean AS ok,
               tc->>'args_preview' AS args_preview,
               (tc->>'output_chars')::int AS output_chars,
               (tc->>'latency_ms')::int AS latency_ms,
               (tc->>'round')::int AS round
        FROM llm_request_logs l,
             LATERAL jsonb_array_elements(
                 CASE WHEN jsonb_typeof(l.debug->'tool_calls') = 'array'
                      THEN l.debug->'tool_calls' ELSE '[]'::jsonb END
             ) tc
        WHERE l.tenant_id = :tid
          AND tc->>'name' = :name
          {ok_filter}
          AND (CAST(:date_from AS timestamptz) IS NULL OR l.created_at >= CAST(:date_from AS timestamptz))
          AND (CAST(:date_to AS timestamptz) IS NULL OR l.created_at <= CAST(:date_to AS timestamptz))
        ORDER BY l.created_at DESC
        LIMIT :lim
    """), {"tid": tenant_id, "name": name, "date_from": date_from, "date_to": date_to, "lim": limit})).mappings().all()
    return [
        ToolCallRecord(
            created_at=r["created_at"],
            chat_id=str(r["chat_id"]) if r["chat_id"] else None,
            message_id=str(r["message_id"]) if r["message_id"] else None,
            ok=bool(r["ok"]),
            args_preview=r["args_preview"],
            output_chars=r["output_chars"],
            latency_ms=r["latency_ms"],
            round=r["round"],
        )
        for r in rows
    ]


@router.get("/groups", response_model=list[str])
async def list_tool_groups(
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Distinct non-null tool group names for this tenant — used by UI filter."""
    await _verify_tenant(tenant_id, db)
    rows = (
        await db.execute(
            select(TenantTool.group)
            .where(
                TenantTool.tenant_id == tenant_id,
                TenantTool.deleted_at.is_(None),
                TenantTool.group.isnot(None),
            )
            .distinct()
        )
    ).all()
    groups = sorted({r[0] for r in rows if r[0]})
    return groups


@router.get("/", response_model=PaginatedResponse[ToolResponse])
async def list_tools(
    tenant_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None),
    group: str | None = Query(None),
    data_source_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    await _verify_tenant(tenant_id, db)

    query = (
        select(TenantTool)
        .where(TenantTool.tenant_id == tenant_id, TenantTool.deleted_at.is_(None))
    )
    if group:
        query = query.where(TenantTool.group == group)
    if data_source_id:
        # data_source_id lives in config_json.x_backend_config.data_source_id (JSONB)
        query = query.where(
            TenantTool.config_json["x_backend_config"]["data_source_id"].astext == data_source_id
        )
    if search and search.strip():
        pattern = f"%{search.strip()}%"
        query = query.where(
            (TenantTool.name.ilike(pattern))
            | (TenantTool.description.ilike(pattern))
            | (TenantTool.group.ilike(pattern))
        )
    query = query.order_by(TenantTool.created_at.desc())

    count_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar()

    items = (
        await db.execute(query.offset((page - 1) * page_size).limit(page_size))
    ).scalars().all()

    return PaginatedResponse[ToolResponse](
        items=[_tool_to_response(t) for t in items],
        total_count=total,
        page=page,
        page_size=page_size,
    )


@router.post("/", response_model=ToolResponse, status_code=status.HTTP_201_CREATED)
async def create_tool(
    tenant_id: uuid.UUID,
    body: ToolCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    await _verify_tenant(tenant_id, db)
    await _validate_tool_payload(tenant_id, body.config_json, db)

    tool = TenantTool(
        tenant_id=tenant_id,
        name=body.name,
        description=body.description,
        config_json=body.config_json,
        tool_type=body.tool_type,
        is_active=body.is_active,
        is_pinned=body.is_pinned,
    )
    db.add(tool)
    await db.flush()
    await db.refresh(tool)
    from app.services.tools.embedder import embed_tool
    background_tasks.add_task(embed_tool, tool.id)
    return _tool_to_response(tool)


@router.get("/{tool_id}", response_model=ToolResponse)
async def get_tool(
    tenant_id: uuid.UUID,
    tool_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    await _verify_tenant(tenant_id, db)

    result = await db.execute(
        select(TenantTool).where(
            TenantTool.id == tool_id,
            TenantTool.tenant_id == tenant_id,
            TenantTool.deleted_at.is_(None),
        )
    )
    tool = result.scalars().first()
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found.")
    return _tool_to_response(tool)


@router.patch("/{tool_id}", response_model=ToolResponse)
async def update_tool(
    tenant_id: uuid.UUID,
    tool_id: uuid.UUID,
    body: ToolUpdate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    await _verify_tenant(tenant_id, db)

    result = await db.execute(
        select(TenantTool).where(
            TenantTool.id == tool_id,
            TenantTool.tenant_id == tenant_id,
            TenantTool.deleted_at.is_(None),
        )
    )
    tool = result.scalars().first()
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found.")

    next_config_json = body.config_json if "config_json" in body.model_fields_set else tool.config_json
    await _validate_tool_payload(tenant_id, next_config_json, db, exclude_tool_id=tool.id)

    update_data = body.model_dump(exclude_unset=True)
    embed_relevant = any(k in update_data for k in ("name", "description", "config_json", "group"))
    for field, value in update_data.items():
        setattr(tool, field, value)
    if embed_relevant:
        # Invalidate old embedding so the next pipeline run uses fresh vector
        tool.embedding = None
        tool.embedding_model = None
        from app.services.tools.embedder import embed_tool
        background_tasks.add_task(embed_tool, tool.id)

    await db.flush()
    await db.refresh(tool)
    return _tool_to_response(tool)


@router.delete("/{tool_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tool(
    tenant_id: uuid.UUID,
    tool_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: AdminUser = Depends(require_role("superadmin", "tenant_admin")),
):
    await _verify_tenant(tenant_id, db)

    result = await db.execute(
        select(TenantTool).where(
            TenantTool.id == tool_id,
            TenantTool.tenant_id == tenant_id,
            TenantTool.deleted_at.is_(None),
        )
    )
    tool = result.scalars().first()
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found.")

    tool.deleted_at = datetime.now(timezone.utc)
    tool.deleted_by = current_user.id
    await db.flush()


@router.post("/test", response_model=ToolTestResponse)
async def test_tool(
    tenant_id: uuid.UUID,
    body: ToolTestRequest,
    db: AsyncSession = Depends(get_db),
):
    await _verify_tenant(tenant_id, db)

    function_name = _get_function_name(body.config_json)
    if not function_name:
        raise HTTPException(status_code=422, detail="config_json.function.name обязателен")

    result = await execute_tool(function_name, body.arguments or {}, body.config_json)
    return ToolTestResponse(success=result.success, output=result.output, error=result.error)


# ─── LLM simulation ──────────────────────────────────────────────────────────

class SimulateRequest(BaseModel):
    message: str
    config_json: dict


class SimulateResponse(BaseModel):
    tool_called: bool
    tool_name: str | None = None
    tool_args: dict | None = None
    tool_result: str | None = None
    tool_error: str | None = None
    llm_thinking: str | None = None
    # text LLM emitted BEFORE the tool call (rare, usually empty)
    llm_preamble: str | None = None
    llm_final_response: str = ""
    model_name: str = ""
    round1_tokens: int = 0
    round2_tokens: int = 0
    total_tokens: int = 0
    latency_ms: int = 0


@router.post("/simulate", response_model=SimulateResponse)
async def simulate_tool_llm(
    tenant_id: uuid.UUID,
    body: SimulateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Run one LLM round with ONLY this tool exposed, execute if called, return trace.

    Round 1: user message → LLM (tool schema in context) → tool_call or direct text
    Round 2: tool result → LLM → final answer
    """
    import json as _json
    import time

    from sqlalchemy import select as _select

    from app.models.tenant_shell_config import TenantShellConfig
    from app.services.llm.model_resolver import resolve_model

    await _verify_tenant(tenant_id, db)

    # Load tenant LLM config
    config = (
        await db.execute(
            _select(TenantShellConfig).where(TenantShellConfig.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Shell config not found for tenant")

    # Resolve provider + model (picks light model — fast for testing)
    try:
        resolved = await resolve_model(str(tenant_id), body.message, db, config)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Model resolution failed: {exc}")

    provider = resolved.provider
    model_name = resolved.model_name

    # Tool definition for the LLM: strip x_backend_config, keep type+function only
    tool_def = {k: v for k, v in body.config_json.items() if k != "x_backend_config"}

    system_prompt = (
        "You are a helpful assistant. A single tool is available. "
        "Use it when the user's request requires it. "
        "If the request can be answered directly — do so without calling the tool."
    )
    messages_r1: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": body.message},
    ]

    t0 = time.monotonic()

    # ── Round 1 ──────────────────────────────────────────────────────────────
    try:
        resp1 = await provider.chat_completion(
            messages=messages_r1,
            model=model_name,
            temperature=0.1,
            max_tokens=2048,
            tools=[tool_def],
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LLM round-1 error: {exc}")

    r1_tokens = int(resp1.total_tokens or 0)

    tool_called = bool(resp1.tool_calls)
    tool_name: str | None = None
    tool_args: dict | None = None
    tool_result_str: str | None = None
    tool_error: str | None = None
    llm_final = resp1.content or ""
    r2_tokens = 0

    if tool_called and resp1.tool_calls:
        tc = resp1.tool_calls[0]  # first call; multi-call is rare for single-tool
        if isinstance(tc, dict):
            fn = tc.get("function") or {}
            tool_name = fn.get("name")
            args_raw = fn.get("arguments", "{}")
            try:
                tool_args = _json.loads(args_raw) if isinstance(args_raw, str) else args_raw
            except Exception:
                tool_args = {"_raw": args_raw}
        tool_call_id: str | None = tc.get("id") if isinstance(tc, dict) else None

        # ── Execute tool ──────────────────────────────────────────────────────
        fn_name = _get_function_name(body.config_json)
        if fn_name:
            exec_result = await execute_tool(fn_name, tool_args or {}, body.config_json)
            if exec_result.success:
                raw_out = exec_result.output
                tool_result_str = (
                    _json.dumps(raw_out, ensure_ascii=False, default=str)
                    if not isinstance(raw_out, str)
                    else raw_out
                )
            else:
                tool_error = exec_result.error
                tool_result_str = f"error: {exec_result.error}"
        else:
            tool_error = "config_json.function.name not found"
            tool_result_str = tool_error

        # ── Round 2: LLM formats the result ──────────────────────────────────
        messages_r2 = messages_r1 + [
            provider.format_assistant_turn(resp1),
            provider.format_tool_result_turn(
                tool_call_id=tool_call_id,
                content=tool_result_str or "",
            ),
        ]
        try:
            resp2 = await provider.chat_completion(
                messages=messages_r2,
                model=model_name,
                temperature=0.3,
                max_tokens=2048,
            )
            llm_final = resp2.content or ""
            r2_tokens = int(resp2.total_tokens or 0)
        except Exception as exc:
            llm_final = f"(round 2 error: {exc})"

    elapsed_ms = int((time.monotonic() - t0) * 1000)

    return SimulateResponse(
        tool_called=tool_called,
        tool_name=tool_name,
        tool_args=tool_args,
        tool_result=tool_result_str,
        tool_error=tool_error,
        llm_thinking=resp1.reasoning,
        llm_preamble=(resp1.content or "") if tool_called else None,
        llm_final_response=llm_final,
        model_name=model_name,
        round1_tokens=r1_tokens,
        round2_tokens=r2_tokens,
        total_tokens=r1_tokens + r2_tokens,
        latency_ms=elapsed_ms,
    )


class SemanticTestRequest(BaseModel):
    query: str
    limit: int = 20


class SemanticTestRow(BaseModel):
    name: str
    cosine: float | None
    tag_bonus: float
    final_score: float
    passes_floor: bool
    matched_tags: list[str]
    description_preview: str
    tool_id: str


class SemanticTestResponse(BaseModel):
    query: str
    floor: float
    embedding_model: str | None
    top_k: int
    results: list[SemanticTestRow]


@router.post("/semantic-test", response_model=SemanticTestResponse)
async def semantic_test(
    tenant_id: uuid.UUID,
    body: SemanticTestRequest,
    db: AsyncSession = Depends(get_db),
):
    """Ad-hoc semantic ranking: feed a query, see which tools rise to the top
    with their cosine + tag-bonus breakdown. Lets admins validate that tags
    and descriptions actually steer ranking the way they expect, without
    having to start a chat."""
    await _verify_tenant(tenant_id, db)
    from app.models.tenant_shell_config import TenantShellConfig
    from app.services.tools.embedder import search_tools, _extract_tool_tags

    cfg = (await db.execute(
        select(TenantShellConfig).where(TenantShellConfig.tenant_id == tenant_id)
    )).scalar_one_or_none()
    if not cfg or not cfg.embedding_model_name:
        raise HTTPException(status_code=422, detail="Embedding model не настроена в shell config")

    floor = float(getattr(cfg, "tool_semantic_floor", 0.5) or 0.5)
    top_k = max(1, min(int(body.limit or 20), 50))

    from app.services.llm.pipeline import TOOL_SEMANTIC_TOPK
    fetch_k = max(top_k, TOOL_SEMANTIC_TOPK)

    rows = await search_tools(
        tenant_id=str(tenant_id),
        query=body.query,
        db=db,
        embedding_model=cfg.embedding_model_name,
        top_k=fetch_k,
    )

    q_lower = (body.query or "").lower()
    out: list[SemanticTestRow] = []
    for t in rows[:top_k]:
        tags = _extract_tool_tags(t)
        # Per-word match: tag is matched if at least one of its words is in query
        import re as _re
        matched_tags: list[str] = []
        for tag in tags:
            words = [w for w in _re.split(r"[\s,;]+", tag.lower()) if len(w) >= 2]
            if any(w in q_lower for w in words):
                matched_tags.append(tag)
        cos = getattr(t, "_semantic_score_cosine", None)
        bonus = getattr(t, "_semantic_tag_bonus", 0.0) or 0.0
        final = getattr(t, "_semantic_score", 0.0) or 0.0
        desc = ((t.config_json or {}).get("function") or {}).get("description") or t.description or ""
        out.append(SemanticTestRow(
            name=t.name,
            cosine=round(float(cos), 3) if cos is not None else None,
            tag_bonus=round(float(bonus), 3),
            final_score=round(float(final), 3),
            passes_floor=float(final) >= floor,
            matched_tags=matched_tags,
            description_preview=desc[:180],
            tool_id=str(t.id),
        ))

    return SemanticTestResponse(
        query=body.query,
        floor=floor,
        embedding_model=cfg.embedding_model_name,
        top_k=top_k,
        results=out,
    )
