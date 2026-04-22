"""
Tenant-facing endpoints for managing private (custom) LLM models.
Authenticated by tenant API key.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import encrypt_value, decrypt_value, mask_secret
from app.models.tenant import Tenant
from app.models.tenant_custom_model import TenantCustomModel
from app.schemas.llm_model import (
    TenantCustomModelCreate,
    TenantCustomModelUpdate,
    TenantCustomModelResponse,
)
from app.schemas.common import PaginatedResponse
from app.api.deps import get_current_tenant_from_key

router = APIRouter(
    prefix="/api/tenants/{tenant_id}/custom-models",
    tags=["tenant-custom-models"],
)


def _verify_tenant_access(tenant_id: uuid.UUID, tenant: Tenant):
    if tenant.id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API key does not belong to this tenant.",
        )


def _model_to_response(m: TenantCustomModel) -> TenantCustomModelResponse:
    masked_key: str | None = None
    if m.api_key_enc:
        try:
            raw = decrypt_value(m.api_key_enc)
            masked_key = mask_secret(raw)
        except Exception:
            masked_key = "****"

    return TenantCustomModelResponse(
        id=str(m.id),
        tenant_id=str(m.tenant_id),
        name=m.name,
        provider_type=m.provider_type,
        base_url=m.base_url,
        api_key_masked=masked_key,
        model_id=m.model_id,
        tier=m.tier,
        supports_tools=m.supports_tools,
        supports_vision=m.supports_vision,
        max_context_tokens=m.max_context_tokens,
        is_active=m.is_active,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


@router.get("/", response_model=PaginatedResponse[TenantCustomModelResponse])
async def list_custom_models(
    tenant_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant_from_key),
):
    _verify_tenant_access(tenant_id, tenant)

    query = (
        select(TenantCustomModel)
        .where(
            TenantCustomModel.tenant_id == tenant_id,
            TenantCustomModel.deleted_at.is_(None),
        )
        .order_by(TenantCustomModel.name)
    )

    count_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar()

    items = (
        await db.execute(query.offset((page - 1) * page_size).limit(page_size))
    ).scalars().all()

    return PaginatedResponse[TenantCustomModelResponse](
        items=[_model_to_response(m) for m in items],
        total_count=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{custom_model_id}", response_model=TenantCustomModelResponse)
async def get_custom_model(
    tenant_id: uuid.UUID,
    custom_model_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant_from_key),
):
    _verify_tenant_access(tenant_id, tenant)

    result = await db.execute(
        select(TenantCustomModel).where(
            TenantCustomModel.id == custom_model_id,
            TenantCustomModel.tenant_id == tenant_id,
            TenantCustomModel.deleted_at.is_(None),
        )
    )
    m = result.scalars().first()
    if not m:
        raise HTTPException(status_code=404, detail="Custom model not found.")
    return _model_to_response(m)


@router.post("/", response_model=TenantCustomModelResponse, status_code=status.HTTP_201_CREATED)
async def create_custom_model(
    tenant_id: uuid.UUID,
    body: TenantCustomModelCreate,
    db: AsyncSession = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant_from_key),
):
    _verify_tenant_access(tenant_id, tenant)

    m = TenantCustomModel(
        tenant_id=tenant_id,
        name=body.name,
        provider_type=body.provider_type,
        base_url=body.base_url,
        model_id=body.model_id,
        tier=body.tier,
        supports_tools=body.supports_tools,
        supports_vision=body.supports_vision,
        max_context_tokens=body.max_context_tokens,
    )
    if body.api_key:
        m.api_key_enc = encrypt_value(body.api_key)

    db.add(m)
    await db.flush()
    await db.refresh(m)
    return _model_to_response(m)


@router.patch("/{custom_model_id}", response_model=TenantCustomModelResponse)
async def update_custom_model(
    tenant_id: uuid.UUID,
    custom_model_id: uuid.UUID,
    body: TenantCustomModelUpdate,
    db: AsyncSession = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant_from_key),
):
    _verify_tenant_access(tenant_id, tenant)

    result = await db.execute(
        select(TenantCustomModel).where(
            TenantCustomModel.id == custom_model_id,
            TenantCustomModel.tenant_id == tenant_id,
            TenantCustomModel.deleted_at.is_(None),
        )
    )
    m = result.scalars().first()
    if not m:
        raise HTTPException(status_code=404, detail="Custom model not found.")

    update_data = body.model_dump(exclude_unset=True)

    if "api_key" in update_data:
        raw_key = update_data.pop("api_key")
        if raw_key:
            m.api_key_enc = encrypt_value(raw_key)
        else:
            m.api_key_enc = None

    for field, value in update_data.items():
        setattr(m, field, value)

    await db.flush()
    await db.refresh(m)
    return _model_to_response(m)


@router.delete("/{custom_model_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_custom_model(
    tenant_id: uuid.UUID,
    custom_model_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant_from_key),
):
    _verify_tenant_access(tenant_id, tenant)

    result = await db.execute(
        select(TenantCustomModel).where(
            TenantCustomModel.id == custom_model_id,
            TenantCustomModel.tenant_id == tenant_id,
            TenantCustomModel.deleted_at.is_(None),
        )
    )
    m = result.scalars().first()
    if not m:
        raise HTTPException(status_code=404, detail="Custom model not found.")

    from datetime import datetime, timezone
    m.deleted_at = datetime.now(timezone.utc)
    await db.flush()
