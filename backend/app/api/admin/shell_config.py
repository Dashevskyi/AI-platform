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
        ontology_json=cfg.ontology_json,
        rules_text=cfg.rules_text,
        temperature=cfg.temperature,
        max_context_messages=cfg.max_context_messages,
        history_budget_tokens=getattr(cfg, "history_budget_tokens", 3000) or 3000,
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
        tool_limit_auto=getattr(cfg, "tool_limit_auto", False),
        tool_limit_max_failures=getattr(cfg, "tool_limit_max_failures", 4),
        tool_limit_max_per_tool=getattr(cfg, "tool_limit_max_per_tool", 4),
        tool_limit_plan_rounds=getattr(cfg, "tool_limit_plan_rounds", 20),
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
        voice_hold_enabled=getattr(cfg, "voice_hold_enabled", None),
        voice_hold_delay_ms=getattr(cfg, "voice_hold_delay_ms", None),
        voice_hold_phrases=getattr(cfg, "voice_hold_phrases", None),
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
        "history_budget_tokens": getattr(cfg, "history_budget_tokens", 3000),
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
        "ontology_json": cfg.ontology_json,
        "response_language": cfg.response_language,
        "debug_enabled": cfg.debug_enabled,
        "timezone": cfg.timezone,
        "tool_semantic_floor": cfg.tool_semantic_floor,
        "tool_routing_temperature": cfg.tool_routing_temperature,
        "lazy_tool_catalog_topk": cfg.lazy_tool_catalog_topk,
        "max_tool_rounds": cfg.max_tool_rounds,
        "tool_limit_auto": getattr(cfg, "tool_limit_auto", False),
        "tool_limit_max_failures": getattr(cfg, "tool_limit_max_failures", 4),
        "tool_limit_max_per_tool": getattr(cfg, "tool_limit_max_per_tool", 4),
        "tool_limit_plan_rounds": getattr(cfg, "tool_limit_plan_rounds", 20),
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

    # Structured ontology is the source of truth — but regenerate the flat
    # ontology_prompt ONLY when ontology_json ACTUALLY changed. The shell form
    # resends the whole config on every save, so an unrelated save (or a stray
    # Enter) must NOT clobber a hand-authored ontology_prompt with a partial
    # structured version.
    if "ontology_json" in update_data and cfg.ontology_json and cfg.ontology_json != previous_payload.get("ontology_json"):
        from app.services.ontology import serialize
        cfg.ontology_prompt = serialize(cfg.ontology_json) or None

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


class OntologyPreviewBody(BaseModel):
    ontology_json: dict | None = None


class OntologyParseBody(BaseModel):
    text: str = ""


class OntologySuggestBody(BaseModel):
    task: str = "Дополнить и улучшить онтологию: глоссарий, примеры, пробелы в покрытии tools."
    ontology_json: dict | None = None
    audit_cases: list[dict] | None = None


class OntologyApplyPatchesBody(BaseModel):
    ontology_json: dict | None = None
    patch_ids: list[str]
    patches: list[dict]


class OntologySnapshotBody(BaseModel):
    ontology_json: dict
    comment: str | None = None


class RoutingFeedbackBody(BaseModel):
    dry_run: bool = False
    days: int = 14
    limit: int = 40
    async_job: bool = False


@router.post("/ontology/preview")
async def ontology_preview(tenant_id: uuid.UUID, body: OntologyPreviewBody) -> dict:
    """Serialize a structured ontology to the flat text the LLM will read.
    Pure/read-only — does NOT save."""
    from app.services.ontology import serialize
    return {"text": serialize(body.ontology_json)}


@router.post("/ontology/parse")
async def ontology_parse(
    tenant_id: uuid.UUID,
    body: OntologyParseBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Best-effort parse flat ontology text into structured form. Does NOT save."""
    await _verify_tenant(tenant_id, db)
    from app.services.ontology import parse_text
    return {"ontology_json": parse_text(body.text or "")}


@router.post("/ontology/import")
async def ontology_import(tenant_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict:
    """Best-effort parse the current flat ontology_prompt into the structured
    form (for bootstrapping the editor). Returns it — does NOT save."""
    await _verify_tenant(tenant_id, db)
    cfg = (await db.execute(
        select(TenantShellConfig).where(TenantShellConfig.tenant_id == tenant_id))).scalars().first()
    from app.services.ontology import parse_text
    return {"ontology_json": parse_text((cfg.ontology_prompt if cfg else "") or "")}


async def _resolve_heavy_model_for_suggest(tenant_id: str, db: AsyncSession, cfg: TenantShellConfig):
    from app.models.tenant_model_config import TenantModelConfig
    from app.services.llm.model_resolver import (
        _load_model_record, _make_provider, _resolve_from_shell_config,
    )
    mc = (await db.execute(
        select(TenantModelConfig).where(TenantModelConfig.tenant_id == uuid.UUID(tenant_id))
    )).scalar_one_or_none()
    if mc:
        for mid, cid in (
            (mc.auto_heavy_model_id, getattr(mc, "auto_heavy_custom_model_id", None)),
            (mc.manual_model_id, mc.manual_custom_model_id),
            (mc.auto_light_model_id, mc.auto_light_custom_model_id),
        ):
            if mid or cid:
                try:
                    record, is_custom = await _load_model_record(mid, cid, db)
                    if record:
                        return _make_provider(record, is_custom)
                except Exception:
                    continue
    return _resolve_from_shell_config(cfg)


async def _load_tenant_tools(tenant_id: uuid.UUID, db: AsyncSession) -> list[dict]:
    from app.models.tenant_tool import TenantTool
    rows = (await db.execute(
        select(TenantTool).where(
            TenantTool.tenant_id == tenant_id,
            TenantTool.deleted_at.is_(None),
            TenantTool.is_active.is_(True),
        ).order_by(TenantTool.name)
    )).scalars().all()
    out = []
    for t in rows:
        fn = {}
        if isinstance(t.config_json, dict):
            fn = (t.config_json.get("function") or {}) if isinstance(t.config_json.get("function"), dict) else {}
        out.append({
            "name": t.name,
            "description": t.description or fn.get("description") or "",
        })
    return out


@router.get("/ontology/tool-call-audit")
async def ontology_tool_call_audit(
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    days: int = Query(14, ge=1, le=90),
    limit: int = Query(80, ge=1, le=200),
    include_logs: bool = Query(True),
    include_audit_cases: bool = Query(True),
    assistant_id: uuid.UUID | None = Query(None),
) -> dict:
    """Erroneous tool calls from logs + failed audit cases — for ontology examples."""
    await _verify_tenant(tenant_id, db)
    from app.services.tool_call_audit import collect_tool_call_audit
    return await collect_tool_call_audit(
        db,
        tenant_id,
        days=days,
        limit=limit,
        include_logs=include_logs,
        include_audit_cases=include_audit_cases,
        assistant_id=assistant_id,
    )


@router.post("/ontology/routing-feedback")
async def ontology_routing_feedback(
    tenant_id: uuid.UUID,
    body: RoutingFeedbackBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Apply audit/log routing failures to tool usage_examples and re-embed."""
    await _verify_tenant(tenant_id, db)
    if body.async_job:
        from app.services.jobs.queue import enqueue as enqueue_job
        await enqueue_job(db, "routing_feedback", {
            "tenant_id": str(tenant_id),
            "days": body.days,
            "limit": body.limit,
            "dry_run": body.dry_run,
        }, tenant_id=tenant_id)
        await db.commit()
        return {"queued": True}
    from app.services.llm.routing_feedback import apply_routing_feedback
    return await apply_routing_feedback(
        db, tenant_id, days=body.days, limit=body.limit, dry_run=body.dry_run,
    )


@router.post("/ontology/suggest")
async def ontology_suggest(
    tenant_id: uuid.UUID,
    body: OntologySuggestBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Ask the tenant's heavy model for structured ontology patches. Does NOT save."""
    await _verify_tenant(tenant_id, db)
    cfg = (await db.execute(
        select(TenantShellConfig).where(TenantShellConfig.tenant_id == tenant_id)
    )).scalars().first()
    if not cfg:
        raise HTTPException(status_code=404, detail="Shell config not found")
    try:
        resolved = await _resolve_heavy_model_for_suggest(str(tenant_id), db, cfg)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Model resolution failed: {exc}") from exc
    tools = await _load_tenant_tools(tenant_id, db)
    ontology = body.ontology_json if body.ontology_json is not None else cfg.ontology_json
    from app.services.ontology_suggest import suggest_patches
    try:
        return await suggest_patches(
            resolved.provider,
            resolved.model_name,
            task=body.task.strip() or "Улучши онтологию.",
            ontology_json=ontology,
            tools=tools,
            system_prompt=cfg.system_prompt,
            audit_cases=body.audit_cases,
            max_tokens=cfg.max_tokens or 6000,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LLM suggest failed: {exc}") from exc


@router.post("/ontology/apply-patches")
async def ontology_apply_patches(body: OntologyApplyPatchesBody) -> dict:
    """Merge accepted patches into ontology_json (pure, does NOT save)."""
    from app.services.ontology_suggest import apply_patches
    selected = {p.get("id") for p in body.patches if isinstance(p, dict)}
    to_apply = [p for p in body.patches if isinstance(p, dict) and p.get("id") in set(body.patch_ids) and p.get("id") in selected]
    return {"ontology_json": apply_patches(body.ontology_json, to_apply)}


@router.post("/ontology/snapshot")
async def ontology_snapshot(
    tenant_id: uuid.UUID,
    body: OntologySnapshotBody,
    current_user: AdminUser = Depends(require_tenant_access),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Save ontology-only snapshot into shell version history (does NOT change live config)."""
    await _verify_tenant(tenant_id, db)
    cfg = (await db.execute(
        select(TenantShellConfig).where(TenantShellConfig.tenant_id == tenant_id)
    )).scalars().first()
    prev_json = (cfg.ontology_json if cfg else None) or None
    comment = (body.comment or "").strip() or "Снимок онтологии"
    if not comment.startswith("[ontology]"):
        comment = f"[ontology] {comment}"
    version = TenantShellConfigVersion(
        tenant_id=tenant_id,
        changed_by=current_user.id,
        previous_payload={"ontology_json": prev_json},
        new_payload={"ontology_json": body.ontology_json},
        comment=comment,
    )
    db.add(version)
    await db.commit()
    await db.refresh(version)
    return {"id": str(version.id), "changed_at": version.changed_at.isoformat()}


@router.get("/ontology/versions")
async def list_ontology_versions(
    tenant_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Shell versions where ontology_json changed (incl. manual snapshots)."""
    await _verify_tenant(tenant_id, db)
    rows = (await db.execute(
        select(TenantShellConfigVersion)
        .where(TenantShellConfigVersion.tenant_id == tenant_id)
        .order_by(TenantShellConfigVersion.changed_at.desc())
        .limit(200)
    )).scalars().all()
    actor_ids = {v.changed_by for v in rows if v.changed_by}
    logins: dict = {}
    if actor_ids:
        logins = dict((await db.execute(
            select(AdminUser.id, AdminUser.login).where(AdminUser.id.in_(actor_ids))
        )).all())
    filtered = []
    for v in rows:
        fields = _changed_fields(v.previous_payload, v.new_payload)
        if "ontology_json" not in fields and not (v.comment or "").startswith("[ontology]"):
            continue
        oj = (v.new_payload or {}).get("ontology_json") or {}
        sections = oj.get("sections") if isinstance(oj, dict) else []
        filtered.append({
            "id": str(v.id),
            "changed_at": v.changed_at.isoformat() if v.changed_at else None,
            "changed_by": logins.get(v.changed_by),
            "comment": v.comment,
            "section_count": len(sections) if isinstance(sections, list) else 0,
        })
    start = (page - 1) * page_size
    page_items = filtered[start:start + page_size]
    return {"items": page_items, "total_count": len(filtered), "page": page, "page_size": page_size}


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
