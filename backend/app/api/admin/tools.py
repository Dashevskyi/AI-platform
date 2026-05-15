"""
Admin CRUD for tenant tools.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import select, func
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
