"""
Admin endpoints for audit logs.
"""
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.admin_audit_log import AdminAuditLog
from app.schemas.common import PaginatedResponse
from app.api.deps import require_role

from pydantic import BaseModel


class AuditLogResponse(BaseModel):
    id: str
    actor_id: str | None
    actor_role: str | None
    action: str
    resource_type: str
    resource_id: str | None
    tenant_id: str | None
    before_json: dict | None
    after_json: dict | None
    created_at: datetime

    model_config = {"from_attributes": True}


router = APIRouter(
    prefix="/api/admin/audit",
    tags=["admin-audit"],
    dependencies=[Depends(require_role("superadmin"))],
)


def _audit_to_response(a: AdminAuditLog) -> AuditLogResponse:
    return AuditLogResponse(
        id=str(a.id),
        actor_id=str(a.actor_id) if a.actor_id else None,
        actor_role=a.actor_role,
        action=a.action,
        resource_type=a.resource_type,
        resource_id=a.resource_id,
        tenant_id=str(a.tenant_id) if a.tenant_id else None,
        before_json=a.before_json,
        after_json=a.after_json,
        created_at=a.created_at,
    )


@router.get("/", response_model=PaginatedResponse[AuditLogResponse])
async def list_audit_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    actor_id: uuid.UUID | None = Query(None),
    action: str | None = Query(None),
    resource_type: str | None = Query(None),
    tenant_id: uuid.UUID | None = Query(None),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    query = select(AdminAuditLog)

    if actor_id:
        query = query.where(AdminAuditLog.actor_id == actor_id)
    if action:
        query = query.where(AdminAuditLog.action == action)
    if resource_type:
        query = query.where(AdminAuditLog.resource_type == resource_type)
    if tenant_id:
        query = query.where(AdminAuditLog.tenant_id == tenant_id)
    if date_from:
        query = query.where(AdminAuditLog.created_at >= date_from)
    if date_to:
        query = query.where(AdminAuditLog.created_at <= date_to)

    query = query.order_by(AdminAuditLog.created_at.desc())

    count_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar()

    items = (
        await db.execute(query.offset((page - 1) * page_size).limit(page_size))
    ).scalars().all()

    return PaginatedResponse[AuditLogResponse](
        items=[_audit_to_response(a) for a in items],
        total_count=total,
        page=page,
        page_size=page_size,
    )
