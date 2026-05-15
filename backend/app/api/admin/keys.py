"""
Admin CRUD for tenant API keys.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import generate_api_key
from app.models.tenant import Tenant
from app.models.tenant_api_key import TenantApiKey
from app.models.tenant_api_key_group import TenantApiKeyGroup
from app.models.tenant_tool import TenantTool
from app.models.chat import Chat
from app.models.llm_request_log import LLMRequestLog
from app.schemas.tenant import TenantApiKeyCreate, TenantApiKeyResponse, TenantApiKeyCreated, TenantApiKeyUpdate
from app.schemas.common import PaginatedResponse
from app.api.deps import require_role, require_tenant_access, require_permission

router = APIRouter(
    prefix="/api/admin/tenants/{tenant_id}/keys",
    tags=["admin-keys"],
    dependencies=[Depends(require_role("superadmin", "tenant_admin")), Depends(require_tenant_access), Depends(require_permission("keys"))],
)


def _key_to_response(k: TenantApiKey) -> TenantApiKeyResponse:
    return TenantApiKeyResponse(
        id=str(k.id),
        tenant_id=str(k.tenant_id),
        name=k.name,
        key_prefix=k.key_prefix,
        group_id=str(k.group_id) if k.group_id else None,
        group_name=getattr(k, "group_name", None),
        memory_prompt=k.memory_prompt,
        allowed_tool_ids=k.allowed_tool_ids,
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


async def _validate_allowed_tool_ids(
    tenant_id: uuid.UUID,
    allowed_tool_ids: list[str] | None,
    db: AsyncSession,
):
    if allowed_tool_ids is None or not allowed_tool_ids:
        return
    try:
        tool_ids = [uuid.UUID(tool_id) for tool_id in allowed_tool_ids]
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Некорректный tool_id в списке прав.") from exc
    rows = (
        await db.execute(
            select(TenantTool.id).where(
                TenantTool.tenant_id == tenant_id,
                TenantTool.deleted_at.is_(None),
                TenantTool.id.in_(tool_ids),
            )
        )
    ).scalars().all()
    found = {str(item) for item in rows}
    missing = [tool_id for tool_id in allowed_tool_ids if tool_id not in found]
    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"Tool not found for permission list: {', '.join(missing)}",
        )


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

    group = None
    if body.group_id:
        group = (
            await db.execute(
                select(TenantApiKeyGroup).where(
                    TenantApiKeyGroup.id == uuid.UUID(body.group_id),
                    TenantApiKeyGroup.tenant_id == tenant_id,
                )
            )
        ).scalars().first()
        if not group:
            raise HTTPException(status_code=404, detail="API key group not found.")
    await _validate_allowed_tool_ids(tenant_id, body.allowed_tool_ids, db)

    raw_key, prefix, key_hash = generate_api_key()
    api_key = TenantApiKey(
        tenant_id=tenant_id,
        name=body.name,
        key_prefix=prefix,
        key_hash=key_hash,
        expires_at=body.expires_at,
        group_id=group.id if group else None,
        memory_prompt=body.memory_prompt,
        allowed_tool_ids=body.allowed_tool_ids,
    )
    db.add(api_key)
    await db.flush()
    await db.refresh(api_key)

    resp = TenantApiKeyCreated(
        id=str(api_key.id),
        tenant_id=str(api_key.tenant_id),
        name=api_key.name,
        key_prefix=api_key.key_prefix,
        group_id=str(api_key.group_id) if api_key.group_id else None,
        group_name=group.name if group else None,
        memory_prompt=api_key.memory_prompt,
        allowed_tool_ids=api_key.allowed_tool_ids,
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
    body: TenantApiKeyUpdate,
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

    group_name = getattr(key, "group_name", None)
    if "allowed_tool_ids" in body.model_fields_set:
        await _validate_allowed_tool_ids(tenant_id, body.allowed_tool_ids, db)
    if "group_id" in body.model_fields_set:
        if body.group_id:
            group = (
                await db.execute(
                    select(TenantApiKeyGroup).where(
                        TenantApiKeyGroup.id == uuid.UUID(body.group_id),
                        TenantApiKeyGroup.tenant_id == tenant_id,
                    )
                )
            ).scalars().first()
            if not group:
                raise HTTPException(status_code=404, detail="API key group not found.")
            key.group_id = group.id
            group_name = group.name
        else:
            key.group_id = None
            group_name = None

    for field, value in body.model_dump(exclude_unset=True).items():
        if field == "group_id":
            continue
        setattr(key, field, value)
    await db.flush()
    await db.refresh(key)
    setattr(key, "group_name", group_name)
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

    # Historical chats should remain readable after key deletion, and logs must
    # not block deletion via foreign keys.
    await db.execute(
        update(Chat)
        .where(
            Chat.tenant_id == tenant_id,
            Chat.api_key_id == key.id,
        )
        .values(api_key_id=None)
    )
    await db.execute(
        update(LLMRequestLog)
        .where(
            LLMRequestLog.tenant_id == tenant_id,
            LLMRequestLog.api_key_id == key.id,
        )
        .values(api_key_id=None)
    )
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
        group_id=old_key.group_id,
        memory_prompt=old_key.memory_prompt,
        allowed_tool_ids=old_key.allowed_tool_ids,
    )
    db.add(new_key)
    await db.flush()
    await db.refresh(new_key)

    # Preserve chat ownership continuity after rotation so the new key can
    # continue working with chats created under the previous raw key.
    await db.execute(
        update(Chat)
        .where(
            Chat.tenant_id == tenant_id,
            Chat.api_key_id == old_key.id,
        )
        .values(api_key_id=new_key.id)
    )

    return TenantApiKeyCreated(
        id=str(new_key.id),
        tenant_id=str(new_key.tenant_id),
        name=new_key.name,
        key_prefix=new_key.key_prefix,
        group_id=str(new_key.group_id) if new_key.group_id else None,
        group_name=None,
        memory_prompt=new_key.memory_prompt,
        allowed_tool_ids=new_key.allowed_tool_ids,
        is_active=new_key.is_active,
        expires_at=new_key.expires_at,
        last_used_at=new_key.last_used_at,
        created_at=new_key.created_at,
        raw_key=raw_key,
    )
