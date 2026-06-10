"""Tenant-scoped description overrides for built-in tools.

The pipeline calls `load_overrides_for_tenant` per request and passes the
result to `builtin_tools_for_payload` / `builtin_tool_config_map` so the
model sees the tenant-customised description (if any).
"""
from __future__ import annotations

import uuid
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.builtin_tool_override import BuiltinToolOverride

logger = logging.getLogger(__name__)


async def load_overrides_for_tenant(
    db: AsyncSession,
    tenant_id: uuid.UUID,
) -> dict[str, str]:
    """Return {tool_name: description} for every override the tenant has
    set. Empty dict = no overrides, fall back to registry defaults."""
    try:
        rows = (
            await db.execute(
                select(BuiltinToolOverride).where(
                    BuiltinToolOverride.tenant_id == tenant_id
                )
            )
        ).scalars().all()
    except Exception:
        logger.exception(
            "load_overrides_for_tenant failed (tenant=%s); using defaults",
            tenant_id,
        )
        return {}
    return {r.tool_name: r.description for r in rows if r.description}
