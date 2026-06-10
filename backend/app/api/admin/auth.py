"""
Admin authentication endpoints.
"""
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.ratelimit import login_limiter
from app.core.security import (
    verify_password,
    create_access_token,
    hash_password,
    set_auth_cookie,
    clear_auth_cookie,
)
from app.models.admin_user import AdminUser
from app.schemas.auth import LoginRequest, TokenResponse, AdminUserResponse, ChangePasswordRequest
from app.api.deps import get_current_admin


def _issue_token(user: AdminUser) -> str:
    return create_access_token({
        "sub": str(user.id),
        "role": user.role,
        "tenant_id": str(user.tenant_id) if user.tenant_id else None,
        "ver": int(user.token_version or 0),
    })

router = APIRouter(prefix="/api/admin/auth", tags=["admin-auth"])


@router.post("/login", response_model=TokenResponse, dependencies=[Depends(login_limiter)])
async def login(body: LoginRequest, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
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
    token = _issue_token(user)
    set_auth_cookie(response, token, request)
    # Token is also returned in the body for back-compat (existing API clients /
    # the old header flow); the web SPA now relies on the HttpOnly cookie.
    return TokenResponse(access_token=token)


@router.post("/logout")
async def logout(
    response: Response,
    current_user: AdminUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Revoke the current token (bump token_version) and clear the cookie."""
    current_user.token_version = int(current_user.token_version or 0) + 1
    await db.flush()
    clear_auth_cookie(response)
    return {"detail": "Logged out."}


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
    request: Request,
    response: Response,
    current_user: AdminUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Authenticated user changes their own password (verifies current first)."""
    if not verify_password(body.current_password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Текущий пароль указан неверно.",
        )
    if len(body.new_password) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Новый пароль слишком короткий (минимум 8 символов).",
        )
    if body.current_password == body.new_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Новый пароль совпадает с текущим.",
        )
    current_user.password_hash = hash_password(body.new_password)
    # Revoke tokens issued before the password change (other sessions), then
    # re-issue a cookie for this session so the user stays logged in.
    current_user.token_version = int(current_user.token_version or 0) + 1
    await db.flush()
    set_auth_cookie(response, _issue_token(current_user), request)
