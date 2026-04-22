"""
Admin CRUD for tenant tools.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.admin_user import AdminUser
from app.models.tenant import Tenant
from app.models.tenant_tool import TenantTool
from app.schemas.tool import ToolCreate, ToolUpdate, ToolResponse
from app.schemas.common import PaginatedResponse
from app.api.deps import require_role

router = APIRouter(
    prefix="/api/admin/tenants/{tenant_id}/tools",
    tags=["admin-tools"],
    dependencies=[Depends(require_role("superadmin", "tenant_admin"))],
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


@router.get("/", response_model=PaginatedResponse[ToolResponse])
async def list_tools(
    tenant_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    await _verify_tenant(tenant_id, db)

    query = (
        select(TenantTool)
        .where(TenantTool.tenant_id == tenant_id, TenantTool.deleted_at.is_(None))
        .order_by(TenantTool.created_at.desc())
    )

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
    db: AsyncSession = Depends(get_db),
):
    await _verify_tenant(tenant_id, db)

    tool = TenantTool(
        tenant_id=tenant_id,
        name=body.name,
        description=body.description,
        config_json=body.config_json,
        tool_type=body.tool_type,
        is_active=body.is_active,
    )
    db.add(tool)
    await db.flush()
    await db.refresh(tool)
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

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(tool, field, value)

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
