"""Admin CRUD for the Assistant layer.

An assistant is a persona/config profile under a tenant: name + a JSONB
`overrides` map (TenantShellConfig field → value) + optional tool-scope.
Effective request config = tenant shell config overlaid with these overrides
(see services/llm/effective_config.py). Every tenant has exactly one default
assistant; it can't be deleted and is used when a request binds no assistant.
"""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.api.deps import require_role, require_tenant_access, require_permission
from app.models.assistant import Assistant

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin/tenants/{tenant_id}/assistants",
    tags=["admin-assistants"],
    dependencies=[
        Depends(require_role("superadmin", "tenant_admin")),
        Depends(require_tenant_access),
        Depends(require_permission("shell_config")),
    ],
)

# Whitelist of TenantShellConfig fields an assistant may override. Excludes
# tenant-wide invariants (embedding model, KB documents, data sources) and
# secrets that need encryption handling beyond this simple JSONB map.
OVERRIDABLE_FIELDS: set[str] = {
    "system_prompt", "ontology_prompt", "rules_text",
    "temperature", "max_tokens", "max_context_messages", "history_budget_tokens", "context_mode",
    "response_language", "enable_thinking", "timezone",
    "memory_enabled", "knowledge_base_enabled", "kb_max_chunks", "kb_inject_auto",
    "recall_cross_chat_enabled",
    "tools_policy", "tool_semantic_floor", "tool_routing_temperature",
    "lazy_tool_catalog_topk", "max_tool_rounds",
    "tool_limit_auto", "tool_limit_max_failures", "tool_limit_max_per_tool", "tool_limit_plan_rounds",
    "tier0_enabled", "tier0_min_tool_score", "tier0_max_score_gap",
    "pii_routing_enabled",
    # Voice / TTS (consumed once voice path is assistant-aware — phase 4).
    "tts_provider", "tts_voice_id", "tts_model", "tts_speed", "tts_pitch", "tts_fish_url",
    "voice_hold_enabled", "voice_hold_delay_ms", "voice_hold_phrases",
    "stt_initial_prompt", "stt_hotwords",
}


class AssistantCreate(BaseModel):
    name: str
    description: str | None = None
    overrides: dict | None = None
    allowed_tool_ids: list[str] | None = None
    is_default: bool | None = None
    is_active: bool | None = None


class AssistantUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    overrides: dict | None = None
    allowed_tool_ids: list[str] | None = None
    is_default: bool | None = None
    is_active: bool | None = None


class AssistantResponse(BaseModel):
    id: str
    tenant_id: str
    name: str
    description: str | None
    is_default: bool
    is_active: bool
    overrides: dict
    allowed_tool_ids: list[str] | None

    model_config = {"from_attributes": True}


def _to_response(a: Assistant) -> AssistantResponse:
    return AssistantResponse(
        id=str(a.id), tenant_id=str(a.tenant_id), name=a.name, description=a.description,
        is_default=a.is_default, is_active=a.is_active,
        overrides=a.overrides or {}, allowed_tool_ids=a.allowed_tool_ids,
    )


def _clean_overrides(raw: dict | None) -> dict:
    """Keep only whitelisted fields; drop null values (= inherit)."""
    if not raw:
        return {}
    return {k: v for k, v in raw.items() if k in OVERRIDABLE_FIELDS and v is not None}


@router.get("/", response_model=list[AssistantResponse])
async def list_assistants(tenant_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> list[AssistantResponse]:
    rows = (await db.execute(
        select(Assistant).where(Assistant.tenant_id == tenant_id).order_by(
            Assistant.is_default.desc(), Assistant.created_at.asc()
        )
    )).scalars().all()
    return [_to_response(a) for a in rows]


@router.post("/", response_model=AssistantResponse)
async def create_assistant(
    tenant_id: uuid.UUID, body: AssistantCreate, db: AsyncSession = Depends(get_db)
) -> AssistantResponse:
    if not (body.name or "").strip():
        raise HTTPException(400, "name is required")
    make_default = bool(body.is_default)
    if make_default:
        await db.execute(
            update(Assistant).where(Assistant.tenant_id == tenant_id).values(is_default=False)
        )
    a = Assistant(
        tenant_id=tenant_id,
        name=body.name.strip(),
        description=body.description,
        overrides=_clean_overrides(body.overrides),
        allowed_tool_ids=body.allowed_tool_ids,
        is_default=make_default,
        is_active=True if body.is_active is None else bool(body.is_active),
    )
    db.add(a)
    await db.flush()
    await db.refresh(a)
    return _to_response(a)


@router.put("/{assistant_id}", response_model=AssistantResponse)
async def update_assistant(
    tenant_id: uuid.UUID, assistant_id: uuid.UUID, body: AssistantUpdate,
    db: AsyncSession = Depends(get_db),
) -> AssistantResponse:
    a = (await db.execute(
        select(Assistant).where(Assistant.id == assistant_id, Assistant.tenant_id == tenant_id)
    )).scalar_one_or_none()
    if not a:
        raise HTTPException(404, "assistant not found")

    data = body.model_dump(exclude_unset=True)
    if "name" in data and data["name"] is not None:
        a.name = data["name"].strip()
    if "description" in data:
        a.description = data["description"]
    if "overrides" in data:
        a.overrides = _clean_overrides(data["overrides"])
    if "allowed_tool_ids" in data:
        a.allowed_tool_ids = data["allowed_tool_ids"]
    if "is_active" in data and data["is_active"] is not None:
        # Don't let the default be deactivated — it's the fallback.
        if a.is_default and not data["is_active"]:
            raise HTTPException(400, "нельзя деактивировать ассистента по умолчанию")
        a.is_active = bool(data["is_active"])
    if data.get("is_default") is True and not a.is_default:
        await db.execute(
            update(Assistant).where(Assistant.tenant_id == tenant_id).values(is_default=False)
        )
        a.is_default = True

    await db.flush()
    await db.refresh(a)
    return _to_response(a)


@router.delete("/{assistant_id}")
async def delete_assistant(
    tenant_id: uuid.UUID, assistant_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> dict:
    a = (await db.execute(
        select(Assistant).where(Assistant.id == assistant_id, Assistant.tenant_id == tenant_id)
    )).scalar_one_or_none()
    if not a:
        raise HTTPException(404, "assistant not found")
    if a.is_default:
        raise HTTPException(400, "нельзя удалить ассистента по умолчанию")
    await db.delete(a)
    await db.flush()
    return {"ok": True}
