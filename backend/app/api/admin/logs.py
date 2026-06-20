"""
Admin endpoints for LLM request logs.
"""
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.tenant import Tenant
from app.models.tenant_api_key import TenantApiKey
from app.models.llm_request_log import LLMRequestLog
from app.schemas.log import LLMLogResponse, LLMLogDetailResponse, LLMLogSummary
from app.schemas.common import PaginatedResponse
from app.api.deps import require_role, require_tenant_access, require_permission

router = APIRouter(
    prefix="/api/admin/tenants/{tenant_id}/logs",
    tags=["admin-logs"],
    dependencies=[Depends(require_role("superadmin", "tenant_admin")), Depends(require_tenant_access), Depends(require_permission("logs"))],
)


def _tool_errors_count(debug) -> int | None:
    """Count failed tool calls in a request's debug trace (None if no trace)."""
    if not isinstance(debug, dict):
        return None
    tcs = debug.get("tool_calls")
    if not isinstance(tcs, list):
        return None
    return sum(1 for tc in tcs if isinstance(tc, dict) and tc.get("ok") is False)


def _request_preview(log: LLMRequestLog, limit: int = 160) -> str | None:
    """Pull the last user message out of the logged request so the logs table
    can show *what* was asked, not just the chat title. Checks normalized then
    raw request; tolerant of both {'messages': [...]} and bare list shapes."""
    for src in (log.normalized_request, log.raw_request):
        if not isinstance(src, dict):
            continue
        msgs = src.get("messages")
        if not isinstance(msgs, list):
            continue
        for m in reversed(msgs):
            if not isinstance(m, dict) or m.get("role") != "user":
                continue
            content = m.get("content")
            text = None
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                # multimodal: concatenate text parts
                parts = [p.get("text") for p in content
                         if isinstance(p, dict) and p.get("type") == "text" and p.get("text")]
                text = " ".join(parts) if parts else None
            if text and text.strip():
                t = " ".join(text.split())
                return t[:limit] + ("…" if len(t) > limit else "")
    return None


def _log_to_response(log: LLMRequestLog) -> LLMLogResponse:
    return LLMLogResponse(
        id=str(log.id),
        tenant_id=str(log.tenant_id),
        chat_id=str(log.chat_id) if log.chat_id else None,
        api_key_id=str(log.api_key_id) if log.api_key_id else None,
        message_id=str(log.message_id) if log.message_id else None,
        correlation_id=log.correlation_id,
        provider_type=log.provider_type,
        model_name=log.model_name,
        status=log.status,
        error_text=log.error_text,
        latency_ms=log.latency_ms,
        time_to_first_token_ms=log.time_to_first_token_ms,
        prompt_tokens=log.prompt_tokens,
        completion_tokens=log.completion_tokens,
        total_tokens=log.total_tokens,
        tool_calls_count=log.tool_calls_count,
        tool_errors_count=_tool_errors_count(log.debug),
        finish_reason=log.finish_reason,
        estimated_cost=log.estimated_cost,
        served_by=log.served_by,
        request_preview=_request_preview(log),
        created_at=log.created_at,
    )


def _log_filters(
    tenant_id, *, provider_type=None, model_name=None, status_filter=None, served_by=None,
    chat_id=None, api_key_id=None, date_from=None, date_to=None, has_tool_calls=None,
    correlation_id=None,
):
    """Shared WHERE clauses so the list and the summary reflect the same view."""
    clauses = [LLMRequestLog.tenant_id == tenant_id]
    if correlation_id:
        clauses.append(LLMRequestLog.correlation_id == correlation_id)
    if provider_type:
        clauses.append(LLMRequestLog.provider_type == provider_type)
    if model_name:
        clauses.append(LLMRequestLog.model_name == model_name)
    if status_filter == "error":
        clauses.append(LLMRequestLog.status != "success")
    elif status_filter:
        clauses.append(LLMRequestLog.status == status_filter)
    if served_by == "llm":
        # NULL (legacy) counts as llm.
        clauses.append(func.coalesce(LLMRequestLog.served_by, "llm") == "llm")
    elif served_by:
        clauses.append(LLMRequestLog.served_by == served_by)
    if chat_id:
        clauses.append(LLMRequestLog.chat_id == chat_id)
    if api_key_id:
        clauses.append(LLMRequestLog.api_key_id == api_key_id)
    if date_from:
        clauses.append(LLMRequestLog.created_at >= date_from)
    if date_to:
        clauses.append(LLMRequestLog.created_at <= date_to)
    if has_tool_calls is True:
        clauses.append(LLMRequestLog.tool_calls_count > 0)
    elif has_tool_calls is False:
        clauses.append((LLMRequestLog.tool_calls_count == 0) | (LLMRequestLog.tool_calls_count.is_(None)))
    return clauses


def _log_to_detail(log: LLMRequestLog) -> LLMLogDetailResponse:
    return LLMLogDetailResponse(
        id=str(log.id),
        tenant_id=str(log.tenant_id),
        chat_id=str(log.chat_id) if log.chat_id else None,
        api_key_id=str(log.api_key_id) if log.api_key_id else None,
        message_id=str(log.message_id) if log.message_id else None,
        correlation_id=log.correlation_id,
        provider_type=log.provider_type,
        model_name=log.model_name,
        status=log.status,
        error_text=log.error_text,
        latency_ms=log.latency_ms,
        time_to_first_token_ms=log.time_to_first_token_ms,
        prompt_tokens=log.prompt_tokens,
        completion_tokens=log.completion_tokens,
        total_tokens=log.total_tokens,
        tool_calls_count=log.tool_calls_count,
        finish_reason=log.finish_reason,
        estimated_cost=log.estimated_cost,
        created_at=log.created_at,
        raw_request=log.raw_request,
        raw_response=log.raw_response,
        normalized_request=log.normalized_request,
        normalized_response=log.normalized_response,
        request_size_bytes=log.request_size_bytes,
        response_size_bytes=log.response_size_bytes,
        context_messages_count=log.context_messages_count,
        context_memory_count=log.context_memory_count,
        context_kb_count=log.context_kb_count,
        context_tools_count=log.context_tools_count,
        tokens_system=log.tokens_system,
        tokens_tools=log.tokens_tools,
        tokens_memory=log.tokens_memory,
        tokens_kb=log.tokens_kb,
        tokens_history=log.tokens_history,
        tokens_user=log.tokens_user,
        debug=log.debug,
    )


async def _verify_tenant(tenant_id: uuid.UUID, db: AsyncSession):
    result = await db.execute(
        select(Tenant).where(Tenant.id == tenant_id, Tenant.deleted_at.is_(None))
    )
    if not result.scalars().first():
        raise HTTPException(status_code=404, detail="Tenant not found.")


async def _verify_api_key(tenant_id: uuid.UUID, api_key_id: uuid.UUID, db: AsyncSession):
    result = await db.execute(
        select(TenantApiKey).where(
            TenantApiKey.id == api_key_id,
            TenantApiKey.tenant_id == tenant_id,
        )
    )
    if not result.scalars().first():
        raise HTTPException(status_code=404, detail="API key not found.")


@router.get("/", response_model=PaginatedResponse[LLMLogResponse])
async def list_logs(
    tenant_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    provider_type: str | None = Query(None),
    model_name: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),  # success | error | <raw status>
    served_by: str | None = Query(None),  # tier0_template | llm
    chat_id: uuid.UUID | None = Query(None),
    api_key_id: uuid.UUID | None = Query(None),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    has_tool_calls: bool | None = Query(None),
    correlation_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    await _verify_tenant(tenant_id, db)
    if api_key_id:
        await _verify_api_key(tenant_id, api_key_id, db)

    clauses = _log_filters(
        tenant_id, provider_type=provider_type, model_name=model_name, status_filter=status_filter,
        served_by=served_by, chat_id=chat_id, api_key_id=api_key_id, date_from=date_from,
        date_to=date_to, has_tool_calls=has_tool_calls, correlation_id=correlation_id,
    )
    query = select(LLMRequestLog).where(*clauses).order_by(LLMRequestLog.created_at.desc())

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar()
    rows = (await db.execute(query.offset((page - 1) * page_size).limit(page_size))).scalars().all()

    return PaginatedResponse[LLMLogResponse](
        items=[_log_to_response(log) for log in rows],
        total_count=total,
        page=page,
        page_size=page_size,
    )


@router.get("/summary", response_model=LLMLogSummary)
async def logs_summary(
    tenant_id: uuid.UUID,
    provider_type: str | None = Query(None),
    model_name: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    served_by: str | None = Query(None),
    chat_id: uuid.UUID | None = Query(None),
    api_key_id: uuid.UUID | None = Query(None),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    has_tool_calls: bool | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Aggregates over the same filter set as the list — drives the stats bar."""
    await _verify_tenant(tenant_id, db)
    clauses = _log_filters(
        tenant_id, provider_type=provider_type, model_name=model_name, status_filter=status_filter,
        served_by=served_by, chat_id=chat_id, api_key_id=api_key_id, date_from=date_from,
        date_to=date_to, has_tool_calls=has_tool_calls,
    )
    row = (await db.execute(select(
        func.count().label("total"),
        func.count().filter(LLMRequestLog.status != "success").label("errors"),
        func.avg(LLMRequestLog.latency_ms).label("avg_latency"),
        func.avg(LLMRequestLog.total_tokens).label("avg_tokens"),
        func.coalesce(func.sum(LLMRequestLog.total_tokens), 0).label("total_tokens"),
        func.coalesce(func.sum(LLMRequestLog.estimated_cost), 0).label("total_cost"),
        func.count().filter(LLMRequestLog.served_by == "tier0_template").label("tier0"),
        func.count().filter(LLMRequestLog.tool_calls_count > 0).label("with_tools"),
    ).where(*clauses))).one()

    total = row.total or 0
    return LLMLogSummary(
        total=total,
        errors=row.errors or 0,
        error_rate=(row.errors / total) if total else 0.0,
        avg_latency_ms=float(row.avg_latency) if row.avg_latency is not None else None,
        avg_total_tokens=float(row.avg_tokens) if row.avg_tokens is not None else None,
        total_tokens=int(row.total_tokens or 0),
        estimated_cost=float(row.total_cost or 0),
        tier0_count=row.tier0 or 0,
        tier0_share=(row.tier0 / total) if total else 0.0,
        with_tool_calls=row.with_tools or 0,
    )


@router.get("/{log_id}", response_model=LLMLogDetailResponse)
async def get_log(
    tenant_id: uuid.UUID,
    log_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    await _verify_tenant(tenant_id, db)

    result = await db.execute(
        select(LLMRequestLog).where(
            LLMRequestLog.id == log_id,
            LLMRequestLog.tenant_id == tenant_id,
        )
    )
    log = result.scalars().first()
    if not log:
        raise HTTPException(status_code=404, detail="Log entry not found.")
    return _log_to_detail(log)
