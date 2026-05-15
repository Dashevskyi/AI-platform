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
from app.schemas.log import LLMLogResponse, LLMLogDetailResponse
from app.schemas.common import PaginatedResponse
from app.api.deps import require_role, require_tenant_access, require_permission

router = APIRouter(
    prefix="/api/admin/tenants/{tenant_id}/logs",
    tags=["admin-logs"],
    dependencies=[Depends(require_role("superadmin", "tenant_admin")), Depends(require_tenant_access), Depends(require_permission("logs"))],
)


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
        finish_reason=log.finish_reason,
        estimated_cost=log.estimated_cost,
        created_at=log.created_at,
    )


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
    status_filter: str | None = Query(None, alias="status"),
    chat_id: uuid.UUID | None = Query(None),
    api_key_id: uuid.UUID | None = Query(None),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    has_tool_calls: bool | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    await _verify_tenant(tenant_id, db)

    query = select(LLMRequestLog).where(LLMRequestLog.tenant_id == tenant_id)

    if provider_type:
        query = query.where(LLMRequestLog.provider_type == provider_type)
    if model_name:
        query = query.where(LLMRequestLog.model_name == model_name)
    if status_filter:
        query = query.where(LLMRequestLog.status == status_filter)
    if chat_id:
        query = query.where(LLMRequestLog.chat_id == chat_id)
    if api_key_id:
        await _verify_api_key(tenant_id, api_key_id, db)
        query = query.where(LLMRequestLog.api_key_id == api_key_id)
    if date_from:
        query = query.where(LLMRequestLog.created_at >= date_from)
    if date_to:
        query = query.where(LLMRequestLog.created_at <= date_to)
    if has_tool_calls is not None:
        if has_tool_calls:
            query = query.where(LLMRequestLog.tool_calls_count > 0)
        else:
            query = query.where(
                (LLMRequestLog.tool_calls_count == 0)
                | (LLMRequestLog.tool_calls_count.is_(None))
            )

    query = query.order_by(LLMRequestLog.created_at.desc())

    count_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar()

    rows = (await db.execute(query.offset((page - 1) * page_size).limit(page_size))).scalars().all()

    return PaginatedResponse[LLMLogResponse](
        items=[_log_to_response(log) for log in rows],
        total_count=total,
        page=page,
        page_size=page_size,
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
