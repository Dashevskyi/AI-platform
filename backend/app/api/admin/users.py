"""
Admin CRUD for admin users (tenant-scoped or global).

- Superadmin can manage all users (any tenant or global).
- Tenant_admin can manage only users of their own tenant_id, with restrictions:
  * cannot create other superadmins;
  * cannot grant permissions outside the allowed list;
  * cannot reassign tenant_id.
"""
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import hash_password
from app.api.deps import get_current_admin, require_role, require_tenant_access
from app.models.admin_user import AdminUser
from app.schemas.common import PaginatedResponse


ALL_PERMISSIONS = [
    "tools",
    "data_sources",
    "keys",
    "model_config",
    "shell_config",
    "kb",
    "memory",
    "chats",
    "logs",
    "users",
]


class AdminUserCreate(BaseModel):
    login: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=4, max_length=200)
    role: Literal["superadmin", "tenant_admin"] = "tenant_admin"
    tenant_id: str | None = None
    permissions: list[str] = Field(default_factory=list)
    is_active: bool = True


class AdminUserUpdate(BaseModel):
    password: str | None = Field(default=None, min_length=4, max_length=200)
    role: Literal["superadmin", "tenant_admin"] | None = None
    tenant_id: str | None = None
    permissions: list[str] | None = None
    is_active: bool | None = None


class AdminUserItem(BaseModel):
    id: str
    login: str
    role: str
    tenant_id: str | None = None
    permissions: list[str] = []
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


def _to_response(u: AdminUser) -> AdminUserItem:
    return AdminUserItem(
        id=str(u.id),
        login=u.login,
        role=u.role,
        tenant_id=str(u.tenant_id) if u.tenant_id else None,
        permissions=list(u.permissions or []),
        is_active=u.is_active,
        created_at=u.created_at,
        updated_at=u.updated_at,
    )


def _validate_perms(perms: list[str]) -> list[str]:
    cleaned = []
    for p in perms:
        if not isinstance(p, str):
            continue
        p = p.strip()
        if not p:
            continue
        if p not in ALL_PERMISSIONS:
            raise HTTPException(status_code=400, detail=f"Unknown permission '{p}'")
        cleaned.append(p)
    # de-dupe, preserve order
    seen = set()
    out = []
    for p in cleaned:
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out


# ============================================================
# Tenant-scoped router (used by tenant_admin and superadmin)
# ============================================================

tenant_router = APIRouter(
    prefix="/api/admin/tenants/{tenant_id}/users",
    tags=["admin-users-tenant"],
    dependencies=[Depends(require_role("superadmin", "tenant_admin")), Depends(require_tenant_access)],
)


@tenant_router.get("/", response_model=PaginatedResponse[AdminUserItem])
async def list_tenant_users(
    tenant_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: AdminUser = Depends(get_current_admin),
):
    if current_user.role == "tenant_admin" and "users" not in (current_user.permissions or []):
        raise HTTPException(status_code=403, detail="Permission 'users' required.")

    query = select(AdminUser).where(AdminUser.tenant_id == tenant_id).order_by(AdminUser.created_at.desc())
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar()
    items = (await db.execute(query.offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return PaginatedResponse[AdminUserItem](
        items=[_to_response(u) for u in items],
        total_count=total or 0,
        page=page,
        page_size=page_size,
    )


@tenant_router.post("/", response_model=AdminUserItem, status_code=status.HTTP_201_CREATED)
async def create_tenant_user(
    tenant_id: uuid.UUID,
    body: AdminUserCreate,
    db: AsyncSession = Depends(get_db),
    current_user: AdminUser = Depends(get_current_admin),
):
    if current_user.role == "tenant_admin":
        if "users" not in (current_user.permissions or []):
            raise HTTPException(status_code=403, detail="Permission 'users' required.")
        if body.role == "superadmin":
            raise HTTPException(status_code=403, detail="tenant_admin cannot create superadmin.")
    if body.role != "tenant_admin":
        raise HTTPException(status_code=400, detail="In tenant scope, only tenant_admin role is allowed.")

    perms = _validate_perms(body.permissions)
    if current_user.role == "tenant_admin":
        # cannot grant beyond own perms
        own = set(current_user.permissions or [])
        extra = set(perms) - own
        if extra:
            raise HTTPException(status_code=403, detail=f"Cannot grant permissions you don't have: {sorted(extra)}")

    # uniqueness
    existing = (await db.execute(select(AdminUser).where(AdminUser.login == body.login))).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="Login already exists.")

    user = AdminUser(
        login=body.login,
        password_hash=hash_password(body.password),
        role=body.role,
        tenant_id=tenant_id,
        permissions=perms,
        is_active=body.is_active,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return _to_response(user)


@tenant_router.patch("/{user_id}", response_model=AdminUserItem)
async def update_tenant_user(
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    body: AdminUserUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: AdminUser = Depends(get_current_admin),
):
    if current_user.role == "tenant_admin" and "users" not in (current_user.permissions or []):
        raise HTTPException(status_code=403, detail="Permission 'users' required.")

    user = (await db.execute(select(AdminUser).where(AdminUser.id == user_id))).scalar_one_or_none()
    if not user or user.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="User not found in this tenant.")

    if current_user.role == "tenant_admin":
        if user.role == "superadmin":
            raise HTTPException(status_code=403, detail="Cannot modify superadmin.")
        if body.role and body.role != "tenant_admin":
            raise HTTPException(status_code=403, detail="Cannot change role to non tenant_admin.")
        if body.tenant_id is not None and body.tenant_id != str(tenant_id):
            raise HTTPException(status_code=403, detail="Cannot reassign tenant_id.")
        if body.permissions is not None:
            own = set(current_user.permissions or [])
            extra = set(_validate_perms(body.permissions)) - own
            if extra:
                raise HTTPException(status_code=403, detail=f"Cannot grant permissions you don't have: {sorted(extra)}")
        # tenant_admin cannot deactivate themselves to lock everyone out — allowed but warn? skip.

    if body.password is not None:
        user.password_hash = hash_password(body.password)
    if body.role is not None:
        user.role = body.role
    if body.tenant_id is not None:
        try:
            user.tenant_id = uuid.UUID(body.tenant_id) if body.tenant_id else None
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid tenant_id.")
    if body.permissions is not None:
        user.permissions = _validate_perms(body.permissions)
    if body.is_active is not None:
        user.is_active = body.is_active

    await db.flush()
    await db.refresh(user)
    return _to_response(user)


@tenant_router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tenant_user(
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: AdminUser = Depends(get_current_admin),
):
    if current_user.role == "tenant_admin" and "users" not in (current_user.permissions or []):
        raise HTTPException(status_code=403, detail="Permission 'users' required.")
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself.")
    user = (await db.execute(select(AdminUser).where(AdminUser.id == user_id))).scalar_one_or_none()
    if not user or user.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="User not found in this tenant.")
    if current_user.role == "tenant_admin" and user.role == "superadmin":
        raise HTTPException(status_code=403, detail="Cannot delete superadmin.")
    await db.delete(user)
    await db.flush()


# ============================================================
# Global router (superadmin only)
# ============================================================

global_router = APIRouter(
    prefix="/api/admin/users",
    tags=["admin-users-global"],
    dependencies=[Depends(require_role("superadmin"))],
)


@global_router.get("/", response_model=PaginatedResponse[AdminUserItem])
async def list_global_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    tenant_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    query = select(AdminUser).order_by(AdminUser.created_at.desc())
    if tenant_id:
        try:
            tid = uuid.UUID(tenant_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid tenant_id.")
        query = query.where(AdminUser.tenant_id == tid)
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar()
    items = (await db.execute(query.offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return PaginatedResponse[AdminUserItem](
        items=[_to_response(u) for u in items],
        total_count=total or 0,
        page=page,
        page_size=page_size,
    )


@global_router.post("/", response_model=AdminUserItem, status_code=status.HTTP_201_CREATED)
async def create_global_user(
    body: AdminUserCreate,
    db: AsyncSession = Depends(get_db),
):
    perms = _validate_perms(body.permissions)
    existing = (await db.execute(select(AdminUser).where(AdminUser.login == body.login))).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="Login already exists.")
    tid: uuid.UUID | None = None
    if body.tenant_id:
        try:
            tid = uuid.UUID(body.tenant_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid tenant_id.")
    if body.role == "tenant_admin" and tid is None:
        raise HTTPException(status_code=400, detail="tenant_admin requires tenant_id.")
    if body.role == "superadmin":
        tid = None
    user = AdminUser(
        login=body.login,
        password_hash=hash_password(body.password),
        role=body.role,
        tenant_id=tid,
        permissions=perms,
        is_active=body.is_active,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return _to_response(user)


@global_router.patch("/{user_id}", response_model=AdminUserItem)
async def update_global_user(
    user_id: uuid.UUID,
    body: AdminUserUpdate,
    db: AsyncSession = Depends(get_db),
):
    user = (await db.execute(select(AdminUser).where(AdminUser.id == user_id))).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    if body.password is not None:
        user.password_hash = hash_password(body.password)
    if body.role is not None:
        user.role = body.role
        if body.role == "superadmin":
            user.tenant_id = None
    if body.tenant_id is not None:
        try:
            user.tenant_id = uuid.UUID(body.tenant_id) if body.tenant_id else None
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid tenant_id.")
    if body.permissions is not None:
        user.permissions = _validate_perms(body.permissions)
    if body.is_active is not None:
        user.is_active = body.is_active
    await db.flush()
    await db.refresh(user)
    return _to_response(user)


@global_router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_global_user(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: AdminUser = Depends(get_current_admin),
):
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself.")
    user = (await db.execute(select(AdminUser).where(AdminUser.id == user_id))).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    await db.delete(user)
    await db.flush()


@global_router.get("/permissions", response_model=list[str])
async def list_permissions():
    return list(ALL_PERMISSIONS)
