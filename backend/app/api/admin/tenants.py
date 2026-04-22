"""
Admin CRUD for tenants.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.admin_user import AdminUser
from app.models.tenant import Tenant
from app.schemas.tenant import TenantCreate, TenantUpdate, TenantResponse
from app.schemas.common import PaginatedResponse
from app.api.deps import require_role

router = APIRouter(
    prefix="/api/admin/tenants",
    tags=["admin-tenants"],
    dependencies=[Depends(require_role("superadmin", "tenant_admin"))],
)


def _tenant_to_response(t: Tenant) -> TenantResponse:
    return TenantResponse(
        id=str(t.id),
        name=t.name,
        slug=t.slug,
        description=t.description,
        is_active=t.is_active,
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


@router.get("/", response_model=PaginatedResponse[TenantResponse])
async def list_tenants(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    sort_by: str = Query("created_at"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    search: str | None = Query(None),
    is_active: bool | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    query = select(Tenant).where(Tenant.deleted_at.is_(None))

    if search:
        pattern = f"%{search}%"
        query = query.where(
            (Tenant.name.ilike(pattern)) | (Tenant.slug.ilike(pattern))
        )
    if is_active is not None:
        query = query.where(Tenant.is_active == is_active)

    # Sorting
    sort_col = getattr(Tenant, sort_by, Tenant.created_at)
    if sort_order == "desc":
        query = query.order_by(sort_col.desc())
    else:
        query = query.order_by(sort_col.asc())

    count_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar()

    items_result = await db.execute(
        query.offset((page - 1) * page_size).limit(page_size)
    )
    items = items_result.scalars().all()

    return PaginatedResponse[TenantResponse](
        items=[_tenant_to_response(t) for t in items],
        total_count=total,
        page=page,
        page_size=page_size,
    )


@router.post("/", response_model=TenantResponse, status_code=status.HTTP_201_CREATED)
async def create_tenant(
    body: TenantCreate,
    db: AsyncSession = Depends(get_db),
):
    # Check slug uniqueness
    existing = await db.execute(
        select(Tenant).where(Tenant.slug == body.slug, Tenant.deleted_at.is_(None))
    )
    if existing.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Tenant with slug '{body.slug}' already exists.",
        )
    tenant = Tenant(
        name=body.name,
        slug=body.slug,
        description=body.description,
        is_active=body.is_active,
    )
    db.add(tenant)
    await db.flush()
    await db.refresh(tenant)
    return _tenant_to_response(tenant)


@router.get("/{tenant_id}", response_model=TenantResponse)
async def get_tenant(
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Tenant).where(Tenant.id == tenant_id, Tenant.deleted_at.is_(None))
    )
    tenant = result.scalars().first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found.")
    return _tenant_to_response(tenant)


@router.patch("/{tenant_id}", response_model=TenantResponse)
async def update_tenant(
    tenant_id: uuid.UUID,
    body: TenantUpdate,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Tenant).where(Tenant.id == tenant_id, Tenant.deleted_at.is_(None))
    )
    tenant = result.scalars().first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found.")

    update_data = body.model_dump(exclude_unset=True)
    if "slug" in update_data:
        existing = await db.execute(
            select(Tenant).where(
                Tenant.slug == update_data["slug"],
                Tenant.id != tenant_id,
                Tenant.deleted_at.is_(None),
            )
        )
        if existing.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Slug '{update_data['slug']}' is already taken.",
            )

    for field, value in update_data.items():
        setattr(tenant, field, value)

    await db.flush()
    await db.refresh(tenant)
    return _tenant_to_response(tenant)


@router.delete("/{tenant_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tenant(
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: AdminUser = Depends(require_role("superadmin", "tenant_admin")),
):
    result = await db.execute(
        select(Tenant).where(Tenant.id == tenant_id, Tenant.deleted_at.is_(None))
    )
    tenant = result.scalars().first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found.")

    tenant.deleted_at = datetime.now(timezone.utc)
    tenant.deleted_by = current_user.id
    await db.flush()
