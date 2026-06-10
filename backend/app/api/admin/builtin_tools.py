"""Admin endpoints for built-in tools.

Builtin tools live in code (`builtin_registry.py`); their handler and
parameter schema are immutable from the UI. Admins can only customise the
`description` field — what the model reads to decide WHEN to call the tool —
on a per-tenant basis via the `builtin_tool_overrides` table.

Endpoints:
  GET    /api/admin/tenants/{tenant_id}/builtin-tools/        → list (merged)
  PATCH  /api/admin/tenants/{tenant_id}/builtin-tools/{name}  → set/replace override
  DELETE /api/admin/tenants/{tenant_id}/builtin-tools/{name}  → drop override (revert to default)
"""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.builtin_tool_override import BuiltinToolOverride
from app.models.tenant import Tenant
from app.api.deps import require_role, require_tenant_access, require_permission
from app.services.tools.builtin_registry import (
    BUILTIN_TOOLS,
    get_builtin_default,
)

router = APIRouter(
    prefix="/api/admin/tenants/{tenant_id}/builtin-tools",
    tags=["admin-builtin-tools"],
    dependencies=[
        Depends(require_role("superadmin", "tenant_admin")),
        Depends(require_tenant_access),
        Depends(require_permission("tools")),
    ],
)


class BuiltinToolItem(BaseModel):
    name: str
    default_description: str
    effective_description: str
    is_overridden: bool
    overridden_at: datetime | None = None
    parameters: dict
    handler: str


class BuiltinToolPatch(BaseModel):
    description: str


async def _verify_tenant(tenant_id: uuid.UUID, db: AsyncSession) -> Tenant:
    result = await db.execute(
        select(Tenant).where(Tenant.id == tenant_id, Tenant.deleted_at.is_(None))
    )
    tenant = result.scalars().first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found.")
    return tenant


@router.get("/", response_model=list[BuiltinToolItem])
async def list_builtin_tools(
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    await _verify_tenant(tenant_id, db)

    overrides = {
        r.tool_name: r
        for r in (
            await db.execute(
                select(BuiltinToolOverride).where(
                    BuiltinToolOverride.tenant_id == tenant_id
                )
            )
        ).scalars().all()
    }

    items: list[BuiltinToolItem] = []
    for t in BUILTIN_TOOLS:
        fn = t.get("function") or {}
        name = fn.get("name")
        default_desc = fn.get("description") or ""
        ov = overrides.get(name)
        items.append(
            BuiltinToolItem(
                name=name,
                default_description=default_desc,
                effective_description=(ov.description if ov else default_desc),
                is_overridden=bool(ov),
                overridden_at=(ov.updated_at if ov else None),
                parameters=fn.get("parameters") or {},
                handler=(t.get("x_backend_config") or {}).get("handler") or name,
            )
        )
    return items


@router.patch("/{tool_name}", response_model=BuiltinToolItem)
async def set_builtin_override(
    tenant_id: uuid.UUID,
    tool_name: str,
    body: BuiltinToolPatch,
    db: AsyncSession = Depends(get_db),
):
    await _verify_tenant(tenant_id, db)

    default = get_builtin_default(tool_name)
    if not default:
        raise HTTPException(status_code=404, detail=f"Builtin tool '{tool_name}' not found.")

    new_desc = (body.description or "").strip()
    if not new_desc:
        raise HTTPException(status_code=422, detail="description must not be empty. Use DELETE to revert to default.")

    existing = (
        await db.execute(
            select(BuiltinToolOverride).where(
                BuiltinToolOverride.tenant_id == tenant_id,
                BuiltinToolOverride.tool_name == tool_name,
            )
        )
    ).scalars().first()

    if existing:
        existing.description = new_desc
        ov = existing
    else:
        ov = BuiltinToolOverride(
            tenant_id=tenant_id,
            tool_name=tool_name,
            description=new_desc,
        )
        db.add(ov)
    await db.flush()
    await db.refresh(ov)

    fn = default.get("function") or {}
    return BuiltinToolItem(
        name=tool_name,
        default_description=fn.get("description") or "",
        effective_description=ov.description,
        is_overridden=True,
        overridden_at=ov.updated_at,
        parameters=fn.get("parameters") or {},
        handler=(default.get("x_backend_config") or {}).get("handler") or tool_name,
    )


@router.delete("/{tool_name}", status_code=204)
async def clear_builtin_override(
    tenant_id: uuid.UUID,
    tool_name: str,
    db: AsyncSession = Depends(get_db),
):
    await _verify_tenant(tenant_id, db)

    default = get_builtin_default(tool_name)
    if not default:
        raise HTTPException(status_code=404, detail=f"Builtin tool '{tool_name}' not found.")

    existing = (
        await db.execute(
            select(BuiltinToolOverride).where(
                BuiltinToolOverride.tenant_id == tenant_id,
                BuiltinToolOverride.tool_name == tool_name,
            )
        )
    ).scalars().first()
    if existing:
        await db.delete(existing)
        await db.flush()
