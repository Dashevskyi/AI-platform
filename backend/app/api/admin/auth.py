"""
Admin authentication endpoints.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import verify_password, create_access_token, hash_password
from app.models.admin_user import AdminUser
from app.schemas.auth import LoginRequest, TokenResponse, AdminUserResponse, ChangePasswordRequest
from app.api.deps import get_current_admin

router = APIRouter(prefix="/api/admin/auth", tags=["admin-auth"])


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AdminUser).where(AdminUser.login == body.login)
    )
    user = result.scalars().first()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный логин или пароль.",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Учётная запись отключена.",
        )
    token = create_access_token({
        "sub": str(user.id),
        "role": user.role,
        "tenant_id": str(user.tenant_id) if user.tenant_id else None,
    })
    return TokenResponse(access_token=token)


@router.post("/logout")
async def logout():
    """JWT is stateless -- nothing to invalidate server-side."""
    return {"detail": "Logged out (stateless)."}


@router.get("/me", response_model=AdminUserResponse)
async def me(current_user: AdminUser = Depends(get_current_admin)):
    return AdminUserResponse(
        id=str(current_user.id),
        login=current_user.login,
        role=current_user.role,
        tenant_id=str(current_user.tenant_id) if current_user.tenant_id else None,
        permissions=list(current_user.permissions or []),
        is_active=current_user.is_active,
    )


@router.get("/permissions", response_model=list[str])
async def list_all_permissions(current_user: AdminUser = Depends(get_current_admin)):
    """Return the full catalogue of permission keys for the Users editor."""
    _ = current_user
    from app.api.admin.users import ALL_PERMISSIONS
    return list(ALL_PERMISSIONS)


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: ChangePasswordRequest,
    current_user: AdminUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Authenticated user changes their own password (verifies current first)."""
    if not verify_password(body.current_password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Текущий пароль указан неверно.",
        )
    if len(body.new_password) < 4:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Новый пароль слишком короткий (минимум 4 символа).",
        )
    if body.current_password == body.new_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Новый пароль совпадает с текущим.",
        )
    current_user.password_hash = hash_password(body.new_password)
    await db.flush()
