"""Admin API mirror: approve/reject risky tool commands from the admin chat UI."""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_role, require_tenant_access, require_permission
from app.core.database import get_db
from app.models.admin_user import AdminUser
from app.schemas.pending_action import PendingActionResponse, to_response
from app.services.tools import pending as pending_svc

router = APIRouter(
    prefix="/api/admin/tenants/{tenant_id}/chats/{chat_id}/pending-actions",
    tags=["admin-pending-actions"],
    dependencies=[Depends(require_role("superadmin", "tenant_admin")), Depends(require_tenant_access), Depends(require_permission("chats"))],
)


@router.get("/", response_model=list[PendingActionResponse])
async def list_actions(
    tenant_id: uuid.UUID,
    chat_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    return [to_response(a) for a in await pending_svc.list_pending(db, tenant_id, chat_id)]


@router.post("/{action_id}/approve", response_model=PendingActionResponse)
async def approve_action(
    tenant_id: uuid.UUID,
    chat_id: uuid.UUID,
    action_id: uuid.UUID,
    current_user: AdminUser = Depends(require_tenant_access),
    db: AsyncSession = Depends(get_db),
):
    try:
        action = await pending_svc.approve(db, tenant_id, chat_id, action_id, f"admin:{current_user.login}")
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return to_response(action)


@router.post("/{action_id}/reject", response_model=PendingActionResponse)
async def reject_action(
    tenant_id: uuid.UUID,
    chat_id: uuid.UUID,
    action_id: uuid.UUID,
    current_user: AdminUser = Depends(require_tenant_access),
    db: AsyncSession = Depends(get_db),
):
    try:
        action = await pending_svc.reject(db, tenant_id, chat_id, action_id, f"admin:{current_user.login}")
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return to_response(action)
