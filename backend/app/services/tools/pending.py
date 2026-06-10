"""Execute a human-approved pending tool action.

On approval the command runs server-side (not via the LLM): we reload the exact
tool config, mark the runtime _approved so the confirmation gate is bypassed,
and dispatch through the normal executor.
"""
import copy
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pending_tool_action import PendingToolAction
from app.models.tenant_tool import TenantTool
from app.services.tools.executor import execute_tool


async def list_pending(db: AsyncSession, tenant_id: uuid.UUID, chat_id: uuid.UUID, status: str = "pending"):
    return list((await db.execute(
        select(PendingToolAction)
        .where(
            PendingToolAction.tenant_id == tenant_id,
            PendingToolAction.chat_id == chat_id,
            PendingToolAction.status == status,
        )
        .order_by(PendingToolAction.created_at.desc())
    )).scalars().all())


async def _load_action(db: AsyncSession, tenant_id, chat_id, action_id) -> PendingToolAction:
    action = (await db.execute(
        select(PendingToolAction).where(
            PendingToolAction.id == action_id,
            PendingToolAction.tenant_id == tenant_id,
            PendingToolAction.chat_id == chat_id,
        )
    )).scalars().first()
    if not action:
        raise LookupError("Запрос на подтверждение не найден")
    if action.status != "pending":
        raise ValueError(f"Запрос уже обработан (статус: {action.status})")
    return action


async def approve(db: AsyncSession, tenant_id, chat_id, action_id, decided_by: str) -> PendingToolAction:
    action = await _load_action(db, tenant_id, chat_id, action_id)
    result = await run_approved_action(db, action)
    action.status = "executed" if result.success else "failed"
    action.result_text = (result.output or "")[:20000] if result.success else None
    action.error_text = None if result.success else (result.error or "")[:5000]
    action.decided_by = decided_by
    action.decided_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(action)
    return action


async def reject(db: AsyncSession, tenant_id, chat_id, action_id, decided_by: str) -> PendingToolAction:
    action = await _load_action(db, tenant_id, chat_id, action_id)
    action.status = "rejected"
    action.decided_by = decided_by
    action.decided_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(action)
    return action


async def run_approved_action(db: AsyncSession, action: PendingToolAction):
    """Reload the tool, bypass the confirmation gate, execute. Returns ToolResult.

    Raises ValueError if the referenced tool no longer exists."""
    tool = (await db.execute(
        select(TenantTool).where(
            TenantTool.tenant_id == action.tenant_id,
            TenantTool.config_json["function"]["name"].astext == action.tool_name,
            TenantTool.is_active.is_(True),
            TenantTool.deleted_at.is_(None),
        )
    )).scalars().first()
    if not tool or not isinstance(tool.config_json, dict):
        raise ValueError(f"Инструмент '{action.tool_name}' не найден или отключён")

    tool_config = copy.deepcopy(tool.config_json)
    runtime = tool_config.setdefault("x_backend_config", {})
    if not isinstance(runtime, dict):
        runtime = {}
        tool_config["x_backend_config"] = runtime
    runtime["_approved"] = True  # bypass the HITL gate in the handler
    tool_config["_context"] = {
        "tenant_id": str(action.tenant_id),
        "chat_id": str(action.chat_id),
        "user_message_id": str(action.message_id) if action.message_id else None,
    }
    return await execute_tool(action.tool_name, dict(action.arguments or {}), tool_config)
