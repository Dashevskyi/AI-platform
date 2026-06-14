"""Effective per-request config = tenant TenantShellConfig + assistant overrides.

The whole pipeline reads behavioural settings as `config.<field>`. To make
those assistant-aware without touching every read site, we wrap the tenant's
TenantShellConfig in an `EffectiveConfig` proxy: attribute access returns the
assistant's override when present, otherwise the tenant default.

Empty overrides ⇒ the proxy is indistinguishable from the raw TenantShellConfig
— which is exactly how existing tenants (one default assistant, overrides={})
keep their current behaviour.

`embedding_model_name` is deliberately NOT overridable (KB/memory/tool indexes
are shared per tenant and must use a single embedding model); overrides for it
are ignored.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assistant import Assistant
from app.models.chat import Chat
from app.models.tenant_shell_config import TenantShellConfig

# Fields that must NOT be overridden per assistant (tenant-wide invariants).
_NON_OVERRIDABLE = frozenset({"embedding_model_name", "tenant_id", "id"})


class EffectiveConfig:
    """Read-only proxy over a TenantShellConfig with assistant overrides applied."""

    __slots__ = (
        "_shell", "_overrides", "assistant_id", "assistant_name",
        "assistant_allowed_tool_ids", "assistant_model_id",
    )

    def __init__(self, shell: TenantShellConfig, assistant: Assistant | None):
        self._shell = shell
        ov = dict(getattr(assistant, "overrides", None) or {}) if assistant else {}
        for k in _NON_OVERRIDABLE:
            ov.pop(k, None)
        # model_id is handled specially by resolve_model (it's an llm_models
        # reference, not a TenantShellConfig field) — pull it out of the field
        # overlay so it doesn't shadow anything.
        self.assistant_model_id = ov.pop("model_id", None)
        self._overrides = ov
        self.assistant_id = getattr(assistant, "id", None)
        self.assistant_name = getattr(assistant, "name", None)
        self.assistant_allowed_tool_ids = getattr(assistant, "allowed_tool_ids", None)

    def __getattr__(self, name: str):
        # Only called when `name` isn't a real attribute/slot — i.e. a config field.
        ov = object.__getattribute__(self, "_overrides")
        if name in ov:
            return ov[name]
        return getattr(object.__getattribute__(self, "_shell"), name)


async def resolve_assistant_for_chat(
    db: AsyncSession, tenant_id: str | uuid.UUID, chat_id: str | uuid.UUID | None
) -> Assistant | None:
    """Pick the assistant for a request: the chat's bound assistant, else the
    tenant's default assistant, else None (→ behaves as raw shell config)."""
    tid = tenant_id if isinstance(tenant_id, uuid.UUID) else uuid.UUID(str(tenant_id))

    if chat_id is not None:
        cid = chat_id if isinstance(chat_id, uuid.UUID) else uuid.UUID(str(chat_id))
        aid = (
            await db.execute(select(Chat.assistant_id).where(Chat.id == cid))
        ).scalar_one_or_none()
        if aid:
            a = (
                await db.execute(select(Assistant).where(Assistant.id == aid))
            ).scalar_one_or_none()
            if a and a.is_active:
                return a

    # Fall back to the tenant default assistant.
    return (
        await db.execute(
            select(Assistant)
            .where(Assistant.tenant_id == tid, Assistant.is_default.is_(True))
            .limit(1)
        )
    ).scalar_one_or_none()


async def resolve_assistant_id_for_new_chat(
    db: AsyncSession,
    tenant_id: str | uuid.UUID,
    explicit_id: str | uuid.UUID | None = None,
    api_key_id: str | uuid.UUID | None = None,
) -> uuid.UUID | None:
    """Pick the assistant to bind a NEW chat to: explicit choice (validated to
    belong to the tenant) → the API key's bound assistant → tenant default."""
    tid = tenant_id if isinstance(tenant_id, uuid.UUID) else uuid.UUID(str(tenant_id))

    if explicit_id:
        eid = explicit_id if isinstance(explicit_id, uuid.UUID) else uuid.UUID(str(explicit_id))
        ok = (await db.execute(
            select(Assistant.id).where(Assistant.id == eid, Assistant.tenant_id == tid)
        )).scalar_one_or_none()
        if ok:
            return eid

    if api_key_id:
        from app.models.tenant_api_key import TenantApiKey
        kid = api_key_id if isinstance(api_key_id, uuid.UUID) else uuid.UUID(str(api_key_id))
        bound = (await db.execute(
            select(TenantApiKey.assistant_id).where(TenantApiKey.id == kid)
        )).scalar_one_or_none()
        if bound:
            return bound

    return (await db.execute(
        select(Assistant.id).where(Assistant.tenant_id == tid, Assistant.is_default.is_(True)).limit(1)
    )).scalar_one_or_none()


async def build_effective_config(
    db: AsyncSession,
    shell: TenantShellConfig,
    tenant_id: str | uuid.UUID,
    chat_id: str | uuid.UUID | None,
) -> EffectiveConfig:
    """Load the request's assistant and overlay its overrides on `shell`."""
    assistant = await resolve_assistant_for_chat(db, tenant_id, chat_id)
    return EffectiveConfig(shell, assistant)
