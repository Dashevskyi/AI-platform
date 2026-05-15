"""
Admin CRUD for tenant memory entries.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.admin_user import AdminUser
from app.models.tenant import Tenant
from app.models.memory_entry import MemoryEntry
from app.schemas.memory import MemoryCreate, MemoryUpdate, MemoryResponse
from app.schemas.common import PaginatedResponse
from app.api.deps import require_role, require_tenant_access, require_permission

router = APIRouter(
    prefix="/api/admin/tenants/{tenant_id}/memory",
    tags=["admin-memory"],
    dependencies=[Depends(require_role("superadmin", "tenant_admin")), Depends(require_tenant_access), Depends(require_permission("memory"))],
)


def _mem_to_response(m: MemoryEntry) -> MemoryResponse:
    return MemoryResponse(
        id=str(m.id),
        tenant_id=str(m.tenant_id),
        chat_id=str(m.chat_id) if m.chat_id else None,
        memory_type=m.memory_type,
        content=m.content,
        metadata_json=m.metadata_json,
        priority=m.priority,
        is_pinned=m.is_pinned,
        expires_at=m.expires_at,
        created_at=m.created_at,
    )


async def _verify_tenant(tenant_id: uuid.UUID, db: AsyncSession) -> Tenant:
    result = await db.execute(
        select(Tenant).where(Tenant.id == tenant_id, Tenant.deleted_at.is_(None))
    )
    tenant = result.scalars().first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found.")
    return tenant


@router.get("/", response_model=PaginatedResponse[MemoryResponse])
async def list_memories(
    tenant_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    memory_type: str | None = Query(None),
    chat_id: uuid.UUID | None = Query(None),
    search: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    await _verify_tenant(tenant_id, db)

    query = (
        select(MemoryEntry)
        .where(
            MemoryEntry.tenant_id == tenant_id,
            MemoryEntry.deleted_at.is_(None),
        )
    )

    if memory_type:
        query = query.where(MemoryEntry.memory_type == memory_type)
    if chat_id:
        query = query.where(MemoryEntry.chat_id == chat_id)
    if search:
        pattern = f"%{search.strip()}%"
        query = query.where(MemoryEntry.content.ilike(pattern))

    query = query.order_by(MemoryEntry.is_pinned.desc(), MemoryEntry.priority.desc(), MemoryEntry.created_at.desc())

    count_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar()

    items = (
        await db.execute(query.offset((page - 1) * page_size).limit(page_size))
    ).scalars().all()

    return PaginatedResponse[MemoryResponse](
        items=[_mem_to_response(m) for m in items],
        total_count=total,
        page=page,
        page_size=page_size,
    )


@router.post("/", response_model=MemoryResponse, status_code=status.HTTP_201_CREATED)
async def create_memory(
    tenant_id: uuid.UUID,
    body: MemoryCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    await _verify_tenant(tenant_id, db)

    mem = MemoryEntry(
        tenant_id=tenant_id,
        chat_id=uuid.UUID(body.chat_id) if body.chat_id else None,
        memory_type=body.memory_type,
        content=body.content,
        metadata_json=body.metadata_json,
        priority=body.priority,
        is_pinned=body.is_pinned,
        expires_at=body.expires_at,
    )
    db.add(mem)
    await db.flush()
    await db.refresh(mem)
    # Schedule embedding in background so the entry is searchable next round.
    from app.services.memory.embedder import embed_memory_entry
    background_tasks.add_task(embed_memory_entry, mem.id)
    return _mem_to_response(mem)


@router.get("/{memory_id}", response_model=MemoryResponse)
async def get_memory(
    tenant_id: uuid.UUID,
    memory_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    await _verify_tenant(tenant_id, db)

    result = await db.execute(
        select(MemoryEntry).where(
            MemoryEntry.id == memory_id,
            MemoryEntry.tenant_id == tenant_id,
            MemoryEntry.deleted_at.is_(None),
        )
    )
    mem = result.scalars().first()
    if not mem:
        raise HTTPException(status_code=404, detail="Memory entry not found.")
    return _mem_to_response(mem)


@router.patch("/{memory_id}", response_model=MemoryResponse)
async def update_memory(
    tenant_id: uuid.UUID,
    memory_id: uuid.UUID,
    body: MemoryUpdate,
    db: AsyncSession = Depends(get_db),
):
    await _verify_tenant(tenant_id, db)

    result = await db.execute(
        select(MemoryEntry).where(
            MemoryEntry.id == memory_id,
            MemoryEntry.tenant_id == tenant_id,
            MemoryEntry.deleted_at.is_(None),
        )
    )
    mem = result.scalars().first()
    if not mem:
        raise HTTPException(status_code=404, detail="Memory entry not found.")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(mem, field, value)

    await db.flush()
    await db.refresh(mem)
    return _mem_to_response(mem)


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(
    tenant_id: uuid.UUID,
    memory_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: AdminUser = Depends(require_role("superadmin", "tenant_admin")),
):
    await _verify_tenant(tenant_id, db)

    result = await db.execute(
        select(MemoryEntry).where(
            MemoryEntry.id == memory_id,
            MemoryEntry.tenant_id == tenant_id,
            MemoryEntry.deleted_at.is_(None),
        )
    )
    mem = result.scalars().first()
    if not mem:
        raise HTTPException(status_code=404, detail="Memory entry not found.")

    mem.deleted_at = datetime.now(timezone.utc)
    mem.deleted_by = current_user.id
    await db.flush()
