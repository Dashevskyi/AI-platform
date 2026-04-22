"""
Admin CRUD for tenant API keys.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import generate_api_key
from app.models.tenant import Tenant
from app.models.tenant_api_key import TenantApiKey
from app.schemas.tenant import TenantApiKeyCreate, TenantApiKeyResponse, TenantApiKeyCreated
from app.schemas.common import PaginatedResponse
from app.api.deps import require_role

router = APIRouter(
    prefix="/api/admin/tenants/{tenant_id}/keys",
    tags=["admin-keys"],
    dependencies=[Depends(require_role("superadmin", "tenant_admin"))],
)


def _key_to_response(k: TenantApiKey) -> TenantApiKeyResponse:
    return TenantApiKeyResponse(
        id=str(k.id),
        tenant_id=str(k.tenant_id),
        name=k.name,
        key_prefix=k.key_prefix,
        is_active=k.is_active,
        expires_at=k.expires_at,
        last_used_at=k.last_used_at,
        created_at=k.created_at,
    )


async def _verify_tenant(tenant_id: uuid.UUID, db: AsyncSession) -> Tenant:
    result = await db.execute(
        select(Tenant).where(Tenant.id == tenant_id, Tenant.deleted_at.is_(None))
    )
    tenant = result.scalars().first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found.")
    return tenant


@router.get("/", response_model=PaginatedResponse[TenantApiKeyResponse])
async def list_keys(
    tenant_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    await _verify_tenant(tenant_id, db)

    query = select(TenantApiKey).where(
        TenantApiKey.tenant_id == tenant_id
    ).order_by(TenantApiKey.created_at.desc())

    count_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar()

    items = (
        await db.execute(query.offset((page - 1) * page_size).limit(page_size))
    ).scalars().all()

    return PaginatedResponse[TenantApiKeyResponse](
        items=[_key_to_response(k) for k in items],
        total_count=total,
        page=page,
        page_size=page_size,
    )


@router.post("/", response_model=TenantApiKeyCreated, status_code=status.HTTP_201_CREATED)
async def create_key(
    tenant_id: uuid.UUID,
    body: TenantApiKeyCreate,
    db: AsyncSession = Depends(get_db),
):
    await _verify_tenant(tenant_id, db)

    raw_key, prefix, key_hash = generate_api_key()
    api_key = TenantApiKey(
        tenant_id=tenant_id,
        name=body.name,
        key_prefix=prefix,
        key_hash=key_hash,
        expires_at=body.expires_at,
    )
    db.add(api_key)
    await db.flush()
    await db.refresh(api_key)

    resp = TenantApiKeyCreated(
        id=str(api_key.id),
        tenant_id=str(api_key.tenant_id),
        name=api_key.name,
        key_prefix=api_key.key_prefix,
        is_active=api_key.is_active,
        expires_at=api_key.expires_at,
        last_used_at=api_key.last_used_at,
        created_at=api_key.created_at,
        raw_key=raw_key,
    )
    return resp


@router.patch("/{key_id}", response_model=TenantApiKeyResponse)
async def deactivate_key(
    tenant_id: uuid.UUID,
    key_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    await _verify_tenant(tenant_id, db)

    result = await db.execute(
        select(TenantApiKey).where(
            TenantApiKey.id == key_id,
            TenantApiKey.tenant_id == tenant_id,
        )
    )
    key = result.scalars().first()
    if not key:
        raise HTTPException(status_code=404, detail="API key not found.")

    key.is_active = False
    await db.flush()
    await db.refresh(key)
    return _key_to_response(key)


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_key(
    tenant_id: uuid.UUID,
    key_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    await _verify_tenant(tenant_id, db)

    result = await db.execute(
        select(TenantApiKey).where(
            TenantApiKey.id == key_id,
            TenantApiKey.tenant_id == tenant_id,
        )
    )
    key = result.scalars().first()
    if not key:
        raise HTTPException(status_code=404, detail="API key not found.")

    await db.delete(key)
    await db.flush()


@router.post("/{key_id}/rotate", response_model=TenantApiKeyCreated)
async def rotate_key(
    tenant_id: uuid.UUID,
    key_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    await _verify_tenant(tenant_id, db)

    result = await db.execute(
        select(TenantApiKey).where(
            TenantApiKey.id == key_id,
            TenantApiKey.tenant_id == tenant_id,
        )
    )
    old_key = result.scalars().first()
    if not old_key:
        raise HTTPException(status_code=404, detail="API key not found.")

    # Deactivate old key
    old_key.is_active = False

    # Generate new key
    raw_key, prefix, key_hash = generate_api_key()
    new_key = TenantApiKey(
        tenant_id=tenant_id,
        name=old_key.name,
        key_prefix=prefix,
        key_hash=key_hash,
        expires_at=old_key.expires_at,
    )
    db.add(new_key)
    await db.flush()
    await db.refresh(new_key)

    return TenantApiKeyCreated(
        id=str(new_key.id),
        tenant_id=str(new_key.tenant_id),
        name=new_key.name,
        key_prefix=new_key.key_prefix,
        is_active=new_key.is_active,
        expires_at=new_key.expires_at,
        last_used_at=new_key.last_used_at,
        created_at=new_key.created_at,
        raw_key=raw_key,
    )
