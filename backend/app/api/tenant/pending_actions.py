"""Tenant API: human-in-the-loop approval of risky tool commands."""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantAuthContext, get_current_tenant_auth_context
from app.core.database import get_db
from app.models.tenant import Tenant
from app.schemas.pending_action import PendingActionResponse, to_response
from app.services.tools import pending as pending_svc

router = APIRouter(prefix="/api/tenants/{tenant_id}/chats/{chat_id}/pending-actions", tags=["tenant-pending-actions"])


def _verify(tenant_id: uuid.UUID, tenant: Tenant) -> None:
    if str(tenant.id) != str(tenant_id):
        raise HTTPException(status_code=403, detail="Forbidden")


@router.get("/", response_model=list[PendingActionResponse])
async def list_actions(
    tenant_id: uuid.UUID,
    chat_id: uuid.UUID,
    auth: TenantAuthContext = Depends(get_current_tenant_auth_context),
    db: AsyncSession = Depends(get_db),
):
    _verify(tenant_id, auth.tenant)
    return [to_response(a) for a in await pending_svc.list_pending(db, tenant_id, chat_id)]


@router.post("/{action_id}/approve", response_model=PendingActionResponse)
async def approve_action(
    tenant_id: uuid.UUID,
    chat_id: uuid.UUID,
    action_id: uuid.UUID,
    auth: TenantAuthContext = Depends(get_current_tenant_auth_context),
    db: AsyncSession = Depends(get_db),
):
    _verify(tenant_id, auth.tenant)
    try:
        action = await pending_svc.approve(db, tenant_id, chat_id, action_id, f"api_key:{auth.api_key.id}")
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
    auth: TenantAuthContext = Depends(get_current_tenant_auth_context),
    db: AsyncSession = Depends(get_db),
):
    _verify(tenant_id, auth.tenant)
    try:
        action = await pending_svc.reject(db, tenant_id, chat_id, action_id, f"api_key:{auth.api_key.id}")
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return to_response(action)
