"""
Auth dependencies for API routers.
"""
import uuid
from datetime import datetime, timezone
from typing import Callable

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import decode_access_token, hash_api_key
from app.models.admin_user import AdminUser
from app.models.tenant import Tenant
from app.models.tenant_api_key import TenantApiKey


async def get_current_admin(
    authorization: str = Header(..., alias="Authorization"),
    db: AsyncSession = Depends(get_db),
) -> AdminUser:
    """Decode JWT from Authorization Bearer header, look up user in DB."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header format. Expected 'Bearer <token>'.",
        )
    token = authorization.removeprefix("Bearer ").strip()
    try:
        payload = decode_access_token(token)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token.",
        )
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token payload missing subject.",
        )
    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid user ID in token.",
        )
    result = await db.execute(select(AdminUser).where(AdminUser.id == uid))
    user = result.scalars().first()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive.",
        )
    return user


async def get_current_tenant_from_key(
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    authorization: str | None = Header(None, alias="Authorization"),
    db: AsyncSession = Depends(get_db),
) -> Tenant:
    """
    Authenticate a tenant via API key.
    Key can be in X-API-Key header or Authorization Bearer header.
    """
    raw_key: str | None = None
    if x_api_key:
        raw_key = x_api_key
    elif authorization and authorization.startswith("Bearer "):
        raw_key = authorization.removeprefix("Bearer ").strip()

    if not raw_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key required. Provide X-API-Key header or Authorization Bearer.",
        )

    key_hash = hash_api_key(raw_key)
    result = await db.execute(
        select(TenantApiKey).where(TenantApiKey.key_hash == key_hash)
    )
    api_key = result.scalars().first()

    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key.",
        )
    if not api_key.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API key is deactivated.",
        )
    now = datetime.now(timezone.utc)
    if api_key.expires_at and api_key.expires_at < now:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API key has expired.",
        )

    # Update last_used_at
    await db.execute(
        update(TenantApiKey)
        .where(TenantApiKey.id == api_key.id)
        .values(last_used_at=now)
    )

    # Fetch tenant
    result = await db.execute(
        select(Tenant).where(Tenant.id == api_key.tenant_id)
    )
    tenant = result.scalars().first()
    if not tenant or not tenant.is_active or tenant.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant not found or inactive.",
        )
    return tenant


def require_role(*roles: str) -> Callable:
    """Return a dependency that checks the current admin user has one of the specified roles."""

    async def _check_role(
        current_user: AdminUser = Depends(get_current_admin),
    ) -> AdminUser:
        if current_user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{current_user.role}' is not authorized. Required: {', '.join(roles)}.",
            )
        return current_user

    return _check_role
