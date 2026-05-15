"""
Admin CRUD for tenant API key groups.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_role, require_tenant_access, require_permission
from app.core.database import get_db
from app.models.tenant import Tenant
from app.models.tenant_api_key import TenantApiKey
from app.models.tenant_api_key_group import TenantApiKeyGroup
from app.models.tenant_tool import TenantTool
from app.schemas.common import PaginatedResponse
from app.schemas.tenant import TenantApiKeyGroupCreate, TenantApiKeyGroupResponse, TenantApiKeyGroupUpdate

router = APIRouter(
    prefix="/api/admin/tenants/{tenant_id}/key-groups",
    tags=["admin-key-groups"],
    dependencies=[Depends(require_role("superadmin", "tenant_admin")), Depends(require_tenant_access), Depends(require_permission("keys"))],
)


def _group_to_response(group: TenantApiKeyGroup) -> TenantApiKeyGroupResponse:
    return TenantApiKeyGroupResponse(
        id=str(group.id),
        tenant_id=str(group.tenant_id),
        name=group.name,
        memory_prompt=group.memory_prompt,
        allowed_tool_ids=group.allowed_tool_ids,
        created_at=group.created_at,
        updated_at=group.updated_at,
    )


async def _verify_tenant(tenant_id: uuid.UUID, db: AsyncSession) -> Tenant:
    tenant = (
        await db.execute(select(Tenant).where(Tenant.id == tenant_id, Tenant.deleted_at.is_(None)))
    ).scalars().first()
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


@router.get("/", response_model=PaginatedResponse[TenantApiKeyGroupResponse])
async def list_key_groups(
    tenant_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    await _verify_tenant(tenant_id, db)
    query = (
        select(TenantApiKeyGroup)
        .where(TenantApiKeyGroup.tenant_id == tenant_id)
        .order_by(TenantApiKeyGroup.name.asc(), TenantApiKeyGroup.created_at.asc())
    )
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar()
    items = (await db.execute(query.offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return PaginatedResponse[TenantApiKeyGroupResponse](
        items=[_group_to_response(group) for group in items],
        total_count=total,
        page=page,
        page_size=page_size,
    )


@router.post("/", response_model=TenantApiKeyGroupResponse, status_code=status.HTTP_201_CREATED)
async def create_key_group(
    tenant_id: uuid.UUID,
    body: TenantApiKeyGroupCreate,
    db: AsyncSession = Depends(get_db),
):
    await _verify_tenant(tenant_id, db)
    await _validate_allowed_tool_ids(tenant_id, body.allowed_tool_ids, db)
    group = TenantApiKeyGroup(
        tenant_id=tenant_id,
        name=body.name,
        memory_prompt=body.memory_prompt,
        allowed_tool_ids=body.allowed_tool_ids,
    )
    db.add(group)
    await db.flush()
    await db.refresh(group)
    return _group_to_response(group)


@router.patch("/{group_id}", response_model=TenantApiKeyGroupResponse)
async def update_key_group(
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    body: TenantApiKeyGroupUpdate,
    db: AsyncSession = Depends(get_db),
):
    await _verify_tenant(tenant_id, db)
    await _validate_allowed_tool_ids(tenant_id, body.allowed_tool_ids, db)
    group = (
        await db.execute(
            select(TenantApiKeyGroup).where(
                TenantApiKeyGroup.id == group_id,
                TenantApiKeyGroup.tenant_id == tenant_id,
            )
        )
    ).scalars().first()
    if not group:
        raise HTTPException(status_code=404, detail="API key group not found.")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(group, field, value)
    await db.flush()
    await db.refresh(group)
    return _group_to_response(group)


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_key_group(
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    await _verify_tenant(tenant_id, db)
    group = (
        await db.execute(
            select(TenantApiKeyGroup).where(
                TenantApiKeyGroup.id == group_id,
                TenantApiKeyGroup.tenant_id == tenant_id,
            )
        )
    ).scalars().first()
    if not group:
        raise HTTPException(status_code=404, detail="API key group not found.")
    if group.allowed_tool_ids is not None:
        affected_keys = (
            await db.execute(
                select(TenantApiKey).where(
                    TenantApiKey.tenant_id == tenant_id,
                    TenantApiKey.group_id == group_id,
                )
            )
        ).scalars().all()
        for key in affected_keys:
            if key.allowed_tool_ids is None:
                key.allowed_tool_ids = list(group.allowed_tool_ids)
    await db.execute(
        update(TenantApiKey)
        .where(TenantApiKey.tenant_id == tenant_id, TenantApiKey.group_id == group_id)
        .values(group_id=None)
    )
    await db.delete(group)
    await db.flush()
