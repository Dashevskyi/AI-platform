"""
Admin endpoints for tenant shell (LLM) configuration.
"""
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
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
        tools_policy=cfg.tools_policy,
        enable_thinking=cfg.enable_thinking,
        response_language=cfg.response_language,
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
        "tools_policy": cfg.tools_policy,
        "enable_thinking": cfg.enable_thinking,
        "ontology_prompt": cfg.ontology_prompt,
        "response_language": cfg.response_language,
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

    # Handle provider_api_key separately
    if "provider_api_key" in update_data:
        raw_key = update_data.pop("provider_api_key")
        if raw_key:
            cfg.provider_api_key_enc = encrypt_value(raw_key)
        else:
            cfg.provider_api_key_enc = None

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
