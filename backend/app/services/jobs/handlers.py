"""Background-job handlers.

Each handler is fully self-contained: it reloads the tenant config / provider
from the (serializable) payload, so jobs survive a restart and never hold live
objects. Registered via @register_job; imported by the worker at startup.
"""
import logging
import uuid

from sqlalchemy import select

from app.core.database import async_session
from app.models.tenant_shell_config import TenantShellConfig
from app.services.jobs.queue import register_job

logger = logging.getLogger(__name__)


async def _load_provider(tenant_id: str):
    """Rebuild (provider, model_name, config) for a tenant from persisted config.
    Provider is a stateless transport wrapper, safe to use after the session
    that built it has closed. Returns (None, None, None) if config is gone."""
    from app.services.llm.model_resolver import resolve_model

    async with async_session() as db:
        config = (await db.execute(
            select(TenantShellConfig).where(TenantShellConfig.tenant_id == uuid.UUID(tenant_id))
        )).scalar_one_or_none()
        if not config:
            return None, None, None
        resolved = await resolve_model(tenant_id, "", db, config)
    return resolved.provider, resolved.model_name, config


@register_job("embed_memory")
async def handle_embed_memory(payload: dict) -> None:
    from app.services.memory.embedder import embed_memory_entry

    memory_id = payload.get("memory_id")
    if memory_id:
        await embed_memory_entry(uuid.UUID(memory_id))


@register_job("history_summary")
async def handle_history_summary(payload: dict) -> None:
    from app.services.llm.pipeline import _update_history_summary_background, _pick_summary_model_name

    tenant_id = payload.get("tenant_id")
    chat_id = payload.get("chat_id")
    if not tenant_id or not chat_id:
        return
    provider, model_name, config = await _load_provider(tenant_id)
    if provider is None:
        logger.info("history_summary: tenant %s config gone, skipping", tenant_id)
        return
    summary_model = _pick_summary_model_name(config, model_name)
    await _update_history_summary_background(
        chat_id=uuid.UUID(chat_id),
        old_messages=payload.get("old_messages") or [],
        existing_summary=payload.get("existing_summary"),
        provider=provider,
        model_name=summary_model,
        message_count_up_to=payload.get("message_count_up_to"),
    )


@register_job("routing_feedback")
async def handle_routing_feedback(payload: dict) -> None:
    from app.services.llm.routing_feedback import apply_routing_feedback

    tenant_id = payload.get("tenant_id")
    if not tenant_id:
        return
    async with async_session() as db:
        await apply_routing_feedback(
            db,
            uuid.UUID(str(tenant_id)),
            days=int(payload.get("days") or 14),
            limit=int(payload.get("limit") or 40),
            dry_run=bool(payload.get("dry_run")),
        )


    from app.services.llm.pipeline import _extract_memory_background, _pick_summary_model_name

    tenant_id = payload.get("tenant_id")
    chat_id = payload.get("chat_id")
    if not tenant_id or not chat_id:
        return
    provider, model_name, config = await _load_provider(tenant_id)
    if provider is None:
        return
    summary_model = _pick_summary_model_name(config, model_name)
    await _extract_memory_background(
        provider, summary_model, uuid.UUID(tenant_id), uuid.UUID(chat_id),
        payload.get("user_content") or "", payload.get("assistant_content") or "",
    )
