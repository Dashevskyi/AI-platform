"""Apply tool-routing audit signals to tool configs + re-embed."""
from __future__ import annotations

import copy
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant_tool import TenantTool
from app.services.tool_call_audit import collect_tool_call_audit
from app.services.tools.embedder import embed_tool

logger = logging.getLogger(__name__)


async def apply_routing_feedback(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    days: int = 14,
    limit: int = 40,
    dry_run: bool = False,
) -> dict:
    """Add usage_examples from audit failures and re-embed affected tools."""
    audit = await collect_tool_call_audit(
        db, tenant_id, days=days, limit=limit,
        include_logs=True, include_audit_cases=True,
    )
    updated_tools: list[str] = []
    skipped: list[str] = []
    examples_added = 0

    for item in audit.get("items") or []:
        if item.get("failure_class") not in ("wrong_tool", "no_tool_call", "tool_error"):
            continue
        tool_name = item.get("expected_tool")
        query = (item.get("query") or "").strip()
        if not tool_name or len(query) < 6:
            continue

        tool = (await db.execute(
            select(TenantTool).where(
                TenantTool.tenant_id == tenant_id,
                TenantTool.name == tool_name,
                TenantTool.deleted_at.is_(None),
                TenantTool.is_active.is_(True),
            )
        )).scalar_one_or_none()
        if not tool:
            skipped.append(f"{tool_name}:not_found")
            continue

        cfg = copy.deepcopy(tool.config_json or {})
        runtime = cfg.setdefault("x_backend_config", {})
        examples = list(runtime.get("usage_examples") or [])
        note = f"[audit:{item.get('failure_class')}]"
        entry = {"query": query[:300], "note": note}
        if any(str(e.get("query", "")).lower() == query.lower() for e in examples if isinstance(e, dict)):
            skipped.append(f"{tool_name}:duplicate")
            continue

        examples.append(entry)
        runtime["usage_examples"] = examples[-20:]
        examples_added += 1

        if dry_run:
            updated_tools.append(tool_name)
            continue

        tool.config_json = cfg
        updated_tools.append(tool_name)

    if not dry_run and updated_tools:
        await db.commit()
        for name in set(updated_tools):
            t = (await db.execute(
                select(TenantTool).where(
                    TenantTool.tenant_id == tenant_id,
                    TenantTool.name == name,
                )
            )).scalar_one_or_none()
            if t:
                try:
                    await embed_tool(t.id)
                except Exception:
                    logger.exception("routing_feedback embed failed for %s", name)

    return {
        "dry_run": dry_run,
        "audit_items": len(audit.get("items") or []),
        "examples_added": examples_added,
        "tools_updated": sorted(set(updated_tools)),
        "skipped": skipped[:30],
    }
