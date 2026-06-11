"""
Admin endpoints for tenant shell (LLM) configuration.
"""
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.common import PaginatedResponse
from app.core.security import encrypt_value, decrypt_value, mask_secret
from app.models.admin_user import AdminUser
from app.models.tenant import Tenant
from app.models.tenant_shell_config import TenantShellConfig
from app.models.tenant_shell_config_version import TenantShellConfigVersion
from app.schemas.shell_config import (
    ShellConfigUpdate,
    ShellConfigResponse,
    TestConnectionRequest,
    TestConnectionResponse,
    VocabRebuildResponse,
)
from app.api.deps import require_role, require_tenant_access, require_permission

router = APIRouter(
    prefix="/api/admin/tenants/{tenant_id}/shell",
    tags=["admin-shell-config"],
    dependencies=[Depends(require_role("superadmin", "tenant_admin")), Depends(require_tenant_access), Depends(require_permission("shell_config"))],
)

MAX_SAFE_TEMPERATURE = 0.7


async def _verify_tenant(tenant_id: uuid.UUID, db: AsyncSession) -> Tenant:
    result = await db.execute(
        select(Tenant).where(Tenant.id == tenant_id, Tenant.deleted_at.is_(None))
    )
    tenant = result.scalars().first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found.")
    return tenant


def _config_to_response(cfg: TenantShellConfig) -> ShellConfigResponse:
    masked_key: str | None = None
    if cfg.provider_api_key_enc:
        try:
            raw = decrypt_value(cfg.provider_api_key_enc)
            masked_key = mask_secret(raw)
        except Exception:
            masked_key = "****"

    tts_key_masked: str | None = None
    if getattr(cfg, "tts_api_key_enc", None):
        try:
            raw = decrypt_value(cfg.tts_api_key_enc)
            tts_key_masked = mask_secret(raw)
        except Exception:
            tts_key_masked = "****"

    return ShellConfigResponse(
        id=str(cfg.id),
        tenant_id=str(cfg.tenant_id),
        provider_type=cfg.provider_type,
        provider_base_url=cfg.provider_base_url,
        provider_api_key_masked=masked_key,
        model_name=cfg.model_name,
        system_prompt=cfg.system_prompt,
        ontology_prompt=cfg.ontology_prompt,
        rules_text=cfg.rules_text,
        temperature=cfg.temperature,
        max_context_messages=cfg.max_context_messages,
        max_tokens=cfg.max_tokens,
        summary_model_name=cfg.summary_model_name,
        context_mode=cfg.context_mode,
        memory_enabled=cfg.memory_enabled,
        knowledge_base_enabled=cfg.knowledge_base_enabled,
        embedding_model_name=cfg.embedding_model_name,
        vision_model_name=cfg.vision_model_name,
        kb_max_chunks=cfg.kb_max_chunks,
        kb_inject_auto=getattr(cfg, "kb_inject_auto", True),
        tools_policy=cfg.tools_policy,
        enable_thinking=cfg.enable_thinking,
        response_language=cfg.response_language,
        debug_enabled=cfg.debug_enabled,
        timezone=cfg.timezone,
        tool_semantic_floor=cfg.tool_semantic_floor,
        tool_routing_temperature=cfg.tool_routing_temperature,
        lazy_tool_catalog_topk=cfg.lazy_tool_catalog_topk,
        max_tool_rounds=cfg.max_tool_rounds,
        tier0_enabled=cfg.tier0_enabled,
        tier0_min_tool_score=cfg.tier0_min_tool_score,
        tier0_max_score_gap=cfg.tier0_max_score_gap,
        pii_routing_enabled=cfg.pii_routing_enabled,
        stt_initial_prompt=cfg.stt_initial_prompt,
        stt_hotwords=cfg.stt_hotwords,
        stt_vocab_source=cfg.stt_vocab_source,
        stt_vocab_source_dsn_masked=mask_secret(decrypt_value(cfg.stt_vocab_source_dsn_enc)) if cfg.stt_vocab_source_dsn_enc else None,
        stt_fuzzy_threshold=cfg.stt_fuzzy_threshold,
        tts_provider=getattr(cfg, "tts_provider", None) or "system",
        tts_api_key_masked=tts_key_masked,
        tts_voice_id=getattr(cfg, "tts_voice_id", None),
        tts_model=getattr(cfg, "tts_model", None),
        tts_speed=getattr(cfg, "tts_speed", None),
        tts_pitch=getattr(cfg, "tts_pitch", None),
        tts_fish_url=getattr(cfg, "tts_fish_url", None),
    )


def _config_to_dict(cfg: TenantShellConfig) -> dict:
    return {
        "provider_type": cfg.provider_type,
        "provider_base_url": cfg.provider_base_url,
        "model_name": cfg.model_name,
        "system_prompt": cfg.system_prompt,
        "rules_text": cfg.rules_text,
        "temperature": cfg.temperature,
        "max_context_messages": cfg.max_context_messages,
        "max_tokens": cfg.max_tokens,
        "summary_model_name": cfg.summary_model_name,
        "context_mode": cfg.context_mode,
        "memory_enabled": cfg.memory_enabled,
        "knowledge_base_enabled": cfg.knowledge_base_enabled,
        "embedding_model_name": cfg.embedding_model_name,
        "vision_model_name": cfg.vision_model_name,
        "kb_max_chunks": cfg.kb_max_chunks,
        "kb_inject_auto": getattr(cfg, "kb_inject_auto", True),
        "tools_policy": cfg.tools_policy,
        "enable_thinking": cfg.enable_thinking,
        "ontology_prompt": cfg.ontology_prompt,
        "response_language": cfg.response_language,
        "debug_enabled": cfg.debug_enabled,
        "timezone": cfg.timezone,
        "tool_semantic_floor": cfg.tool_semantic_floor,
        "tool_routing_temperature": cfg.tool_routing_temperature,
        "lazy_tool_catalog_topk": cfg.lazy_tool_catalog_topk,
        "max_tool_rounds": cfg.max_tool_rounds,
        "tier0_enabled": cfg.tier0_enabled,
        "tier0_min_tool_score": cfg.tier0_min_tool_score,
        "tier0_max_score_gap": cfg.tier0_max_score_gap,
        "pii_routing_enabled": cfg.pii_routing_enabled,
        "stt_initial_prompt": cfg.stt_initial_prompt,
        "stt_hotwords": cfg.stt_hotwords,
        "stt_vocab_source": cfg.stt_vocab_source,
        "stt_fuzzy_threshold": cfg.stt_fuzzy_threshold,
    }


@router.get("/", response_model=ShellConfigResponse)
async def get_shell_config(
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    await _verify_tenant(tenant_id, db)

    result = await db.execute(
        select(TenantShellConfig).where(TenantShellConfig.tenant_id == tenant_id)
    )
    cfg = result.scalars().first()

    if not cfg:
        # Create default config
        cfg = TenantShellConfig(tenant_id=tenant_id)
        db.add(cfg)
        await db.flush()
        await db.refresh(cfg)

    return _config_to_response(cfg)


@router.put("/", response_model=ShellConfigResponse)
async def update_shell_config(
    tenant_id: uuid.UUID,
    body: ShellConfigUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: AdminUser = Depends(require_role("superadmin", "tenant_admin")),
):
    await _verify_tenant(tenant_id, db)

    result = await db.execute(
        select(TenantShellConfig).where(TenantShellConfig.tenant_id == tenant_id)
    )
    cfg = result.scalars().first()

    if not cfg:
        cfg = TenantShellConfig(tenant_id=tenant_id)
        db.add(cfg)
        await db.flush()
        await db.refresh(cfg)

    previous_payload = _config_to_dict(cfg)

    update_data = body.model_dump(exclude_unset=True)

    # Handle provider_api_key separately (encrypted)
    if "provider_api_key" in update_data:
        raw_key = update_data.pop("provider_api_key")
        if raw_key:
            cfg.provider_api_key_enc = encrypt_value(raw_key)
        else:
            cfg.provider_api_key_enc = None

    # Handle tts_api_key separately (encrypted ElevenLabs key)
    if "tts_api_key" in update_data:
        raw_tts_key = update_data.pop("tts_api_key")
        if raw_tts_key:
            cfg.tts_api_key_enc = encrypt_value(raw_tts_key)
        else:
            cfg.tts_api_key_enc = None

    # Handle stt_vocab_source_dsn separately (encrypted)
    if "stt_vocab_source_dsn" in update_data:
        raw_dsn = update_data.pop("stt_vocab_source_dsn")
        if raw_dsn:
            cfg.stt_vocab_source_dsn_enc = encrypt_value(raw_dsn)
        else:
            cfg.stt_vocab_source_dsn_enc = None
        # Invalidate vocab cache when DSN changes
        from app.services.stt_normalizer import invalidate_vocab_cache
        invalidate_vocab_cache(tenant_id)

    # Invalidate vocab cache when source config changes
    if "stt_vocab_source" in update_data:
        from app.services.stt_normalizer import invalidate_vocab_cache
        invalidate_vocab_cache(tenant_id)

    for field, value in update_data.items():
        if field == "temperature" and value is not None:
            value = min(float(value), MAX_SAFE_TEMPERATURE)
        setattr(cfg, field, value)

    await db.flush()
    await db.refresh(cfg)

    new_payload = _config_to_dict(cfg)

    # Save version history
    version = TenantShellConfigVersion(
        tenant_id=tenant_id,
        changed_by=current_user.id,
        previous_payload=previous_payload,
        new_payload=new_payload,
    )
    db.add(version)
    await db.flush()

    return _config_to_response(cfg)


class VersionListItem(BaseModel):
    id: str
    changed_at: datetime
    changed_by: str | None  # admin login
    comment: str | None
    changed_fields: list[str]


class VersionDetail(BaseModel):
    id: str
    changed_at: datetime
    changed_by: str | None
    comment: str | None
    previous_payload: dict | None
    new_payload: dict


def _changed_fields(prev: dict | None, new: dict | None) -> list[str]:
    prev = prev or {}
    new = new or {}
    return sorted(k for k in (set(new) | set(prev)) if prev.get(k) != new.get(k))


@router.get("/versions", response_model=PaginatedResponse[VersionListItem])
async def list_versions(
    tenant_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """History of shell-config changes (newest first), with who changed what."""
    await _verify_tenant(tenant_id, db)
    base = select(TenantShellConfigVersion).where(TenantShellConfigVersion.tenant_id == tenant_id)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar()
    rows = (await db.execute(
        base.order_by(TenantShellConfigVersion.changed_at.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()

    actor_ids = {v.changed_by for v in rows if v.changed_by}
    logins: dict = {}
    if actor_ids:
        logins = dict((await db.execute(
            select(AdminUser.id, AdminUser.login).where(AdminUser.id.in_(actor_ids))
        )).all())

    items = [
        VersionListItem(
            id=str(v.id),
            changed_at=v.changed_at,
            changed_by=logins.get(v.changed_by),
            comment=v.comment,
            changed_fields=_changed_fields(v.previous_payload, v.new_payload),
        )
        for v in rows
    ]
    return PaginatedResponse[VersionListItem](items=items, total_count=total, page=page, page_size=page_size)


@router.get("/versions/{version_id}", response_model=VersionDetail)
async def get_version(
    tenant_id: uuid.UUID,
    version_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    await _verify_tenant(tenant_id, db)
    v = (await db.execute(
        select(TenantShellConfigVersion).where(
            TenantShellConfigVersion.id == version_id,
            TenantShellConfigVersion.tenant_id == tenant_id,
        )
    )).scalars().first()
    if not v:
        raise HTTPException(status_code=404, detail="Версия не найдена.")
    login = None
    if v.changed_by:
        login = (await db.execute(select(AdminUser.login).where(AdminUser.id == v.changed_by))).scalar()
    return VersionDetail(
        id=str(v.id), changed_at=v.changed_at, changed_by=login, comment=v.comment,
        previous_payload=v.previous_payload, new_payload=v.new_payload,
    )


@router.post("/versions/{version_id}/restore", response_model=ShellConfigResponse)
async def restore_version(
    tenant_id: uuid.UUID,
    version_id: uuid.UUID,
    current_user: AdminUser = Depends(require_tenant_access),
    db: AsyncSession = Depends(get_db),
):
    """Roll the shell config back to a stored version's payload. The restore is
    itself recorded as a new version (so it's undoable)."""
    await _verify_tenant(tenant_id, db)
    v = (await db.execute(
        select(TenantShellConfigVersion).where(
            TenantShellConfigVersion.id == version_id,
            TenantShellConfigVersion.tenant_id == tenant_id,
        )
    )).scalars().first()
    if not v or not isinstance(v.new_payload, dict):
        raise HTTPException(status_code=404, detail="Версия не найдена.")

    cfg = (await db.execute(
        select(TenantShellConfig).where(TenantShellConfig.tenant_id == tenant_id)
    )).scalars().first()
    if not cfg:
        raise HTTPException(status_code=404, detail="Конфигурация не найдена.")

    previous_payload = _config_to_dict(cfg)
    _readonly = {"id", "tenant_id", "created_at", "updated_at"}
    for field, value in v.new_payload.items():
        if field in _readonly:
            continue
        if hasattr(cfg, field):
            if field == "temperature" and value is not None:
                value = min(float(value), MAX_SAFE_TEMPERATURE)
            setattr(cfg, field, value)
    await db.flush()
    await db.refresh(cfg)

    db.add(TenantShellConfigVersion(
        tenant_id=tenant_id,
        changed_by=current_user.id,
        previous_payload=previous_payload,
        new_payload=_config_to_dict(cfg),
        comment=f"Восстановлено из версии от {v.changed_at:%Y-%m-%d %H:%M}",
    ))
    await db.flush()
    return _config_to_response(cfg)


@router.post("/test-connection", response_model=TestConnectionResponse)
async def test_connection(
    tenant_id: uuid.UUID,
    body: TestConnectionRequest | None = None,
    db: AsyncSession = Depends(get_db),
):
    await _verify_tenant(tenant_id, db)

    # If no body provided, use saved config
    if not body or not body.provider_type:
        result = await db.execute(
            select(TenantShellConfig).where(TenantShellConfig.tenant_id == tenant_id)
        )
        cfg = result.scalars().first()
        if not cfg:
            return TestConnectionResponse(success=False, message="Конфигурация оболочки не найдена.")
        provider_type = cfg.provider_type
        base_url = (cfg.provider_base_url or "").rstrip("/")
        api_key = None
        if cfg.provider_api_key_enc:
            try:
                api_key = decrypt_value(cfg.provider_api_key_enc)
            except Exception:
                pass
    else:
        provider_type = body.provider_type
        base_url = (body.provider_base_url or "").rstrip("/")
        api_key = body.provider_api_key

    if not base_url:
        # For ollama, use default
        if provider_type == "ollama":
            base_url = "http://localhost:11434"
        else:
            return TestConnectionResponse(success=False, message="URL провайдера не указан.")

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            if provider_type == "ollama":
                resp = await client.get(f"{base_url}/api/tags")
                resp.raise_for_status()
                data = resp.json()
                models = [m.get("name", "") for m in data.get("models", [])]
                return TestConnectionResponse(
                    success=True,
                    message=f"Подключено к Ollama. Найдено моделей: {len(models)}.",
                    models=models,
                )
            else:
                headers = {}
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"
                resp = await client.get(f"{base_url}/models", headers=headers)
                resp.raise_for_status()
                data = resp.json()
                models_list = data.get("data", [])
                models = [m.get("id", "") for m in models_list]
                return TestConnectionResponse(
                    success=True,
                    message=f"Подключено. Найдено моделей: {len(models)}.",
                    models=models,
                )
    except httpx.HTTPStatusError as exc:
        return TestConnectionResponse(
            success=False,
            message=f"Ошибка HTTP {exc.response.status_code}: {exc.response.text[:300]}",
        )
    except Exception as exc:
        return TestConnectionResponse(
            success=False,
            message=f"Ошибка соединения: {str(exc)[:300]}",
        )


@router.get("/models", response_model=TestConnectionResponse)
async def list_models(
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    await _verify_tenant(tenant_id, db)

    result = await db.execute(
        select(TenantShellConfig).where(TenantShellConfig.tenant_id == tenant_id)
    )
    cfg = result.scalars().first()
    if not cfg:
        raise HTTPException(status_code=404, detail="Shell config not found. Create config first.")

    base_url = (cfg.provider_base_url or "").rstrip("/")
    if not base_url:
        return TestConnectionResponse(success=False, message="No provider_base_url configured.")

    api_key: str | None = None
    if cfg.provider_api_key_enc:
        try:
            api_key = decrypt_value(cfg.provider_api_key_enc)
        except Exception:
            pass

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            if cfg.provider_type == "ollama":
                resp = await client.get(f"{base_url}/api/tags")
                resp.raise_for_status()
                data = resp.json()
                models = [m.get("name", "") for m in data.get("models", [])]
                return TestConnectionResponse(
                    success=True,
                    message=f"Found {len(models)} model(s).",
                    models=models,
                )
            else:
                headers = {}
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"
                resp = await client.get(f"{base_url}/models", headers=headers)
                resp.raise_for_status()
                data = resp.json()
                models_list = data.get("data", [])
                models = [m.get("id", "") for m in models_list]
                return TestConnectionResponse(
                    success=True,
                    message=f"Found {len(models)} model(s).",
                    models=models,
                )
    except Exception as exc:
        return TestConnectionResponse(
            success=False,
            message=f"Connection failed: {str(exc)[:300]}",
        )


@router.post("/rebuild-stt-vocab", response_model=VocabRebuildResponse)
async def rebuild_stt_vocab(
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Force-reload the STT vocabulary from the configured source and return a sample.

    Use this button from the admin UI to verify the source is working and to
    warm the cache manually (e.g. after adding new subscribers).
    """
    await _verify_tenant(tenant_id, db)

    result = await db.execute(
        select(TenantShellConfig).where(TenantShellConfig.tenant_id == tenant_id)
    )
    cfg = result.scalars().first()
    if not cfg:
        raise HTTPException(status_code=404, detail="Shell config not found.")
    if not cfg.stt_vocab_source:
        raise HTTPException(status_code=400, detail="Источник словаря не настроен (stt_vocab_source).")

    from app.services.stt_normalizer import get_tenant_vocab
    import time

    try:
        terms = await get_tenant_vocab(
            tenant_id,
            cfg.stt_vocab_source,
            cfg.stt_vocab_source_dsn_enc,
            force_refresh=True,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Ошибка загрузки словаря: {str(exc)[:300]}")

    sample = terms[:20]
    return VocabRebuildResponse(
        terms_count=len(terms),
        sample=sample,
        cached_at=time.time(),
    )
