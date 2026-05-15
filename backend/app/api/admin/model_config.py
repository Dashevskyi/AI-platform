"""
Admin endpoints for tenant model configuration (manual/auto mode selection).
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.tenant import Tenant
from app.models.llm_model import LLMModel
from app.models.tenant_custom_model import TenantCustomModel
from app.models.tenant_model_config import TenantModelConfig
from app.schemas.llm_model import (
    TenantModelConfigUpdate,
    TenantModelConfigResponse,
)
from app.api.deps import require_role, require_tenant_access, require_permission

router = APIRouter(
    prefix="/api/admin/tenants/{tenant_id}/model-config",
    tags=["admin-model-config"],
    dependencies=[Depends(require_role("superadmin", "tenant_admin")), Depends(require_tenant_access), Depends(require_permission("model_config"))],
)


async def _verify_tenant(tenant_id: uuid.UUID, db: AsyncSession) -> Tenant:
    result = await db.execute(
        select(Tenant).where(Tenant.id == tenant_id, Tenant.deleted_at.is_(None))
    )
    tenant = result.scalars().first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found.")
    return tenant


async def _resolve_model_name(model_id: uuid.UUID | None, db: AsyncSession, table=LLMModel) -> str | None:
    if not model_id:
        return None
    result = await db.execute(select(table.name).where(table.id == model_id))
    row = result.first()
    return row[0] if row else None


async def _config_to_response(cfg: TenantModelConfig, db: AsyncSession) -> TenantModelConfigResponse:
    return TenantModelConfigResponse(
        id=str(cfg.id),
        tenant_id=str(cfg.tenant_id),
        mode=cfg.mode,
        manual_model_id=str(cfg.manual_model_id) if cfg.manual_model_id else None,
        manual_custom_model_id=str(cfg.manual_custom_model_id) if cfg.manual_custom_model_id else None,
        auto_light_model_id=str(cfg.auto_light_model_id) if cfg.auto_light_model_id else None,
        auto_heavy_model_id=str(cfg.auto_heavy_model_id) if cfg.auto_heavy_model_id else None,
        auto_light_custom_model_id=str(cfg.auto_light_custom_model_id) if cfg.auto_light_custom_model_id else None,
        auto_heavy_custom_model_id=str(cfg.auto_heavy_custom_model_id) if cfg.auto_heavy_custom_model_id else None,
        complexity_threshold=cfg.complexity_threshold,
        manual_model_name=await _resolve_model_name(cfg.manual_model_id, db, LLMModel),
        manual_custom_model_name=await _resolve_model_name(cfg.manual_custom_model_id, db, TenantCustomModel),
        auto_light_model_name=await _resolve_model_name(cfg.auto_light_model_id, db, LLMModel),
        auto_heavy_model_name=await _resolve_model_name(cfg.auto_heavy_model_id, db, LLMModel),
        auto_light_custom_model_name=await _resolve_model_name(cfg.auto_light_custom_model_id, db, TenantCustomModel),
        auto_heavy_custom_model_name=await _resolve_model_name(cfg.auto_heavy_custom_model_id, db, TenantCustomModel),
    )


@router.get("/", response_model=TenantModelConfigResponse)
async def get_model_config(
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    await _verify_tenant(tenant_id, db)

    result = await db.execute(
        select(TenantModelConfig).where(TenantModelConfig.tenant_id == tenant_id)
    )
    cfg = result.scalars().first()

    if not cfg:
        cfg = TenantModelConfig(tenant_id=tenant_id)
        db.add(cfg)
        await db.flush()
        await db.refresh(cfg)

    return await _config_to_response(cfg, db)


@router.put("/", response_model=TenantModelConfigResponse)
async def update_model_config(
    tenant_id: uuid.UUID,
    body: TenantModelConfigUpdate,
    db: AsyncSession = Depends(get_db),
):
    await _verify_tenant(tenant_id, db)

    result = await db.execute(
        select(TenantModelConfig).where(TenantModelConfig.tenant_id == tenant_id)
    )
    cfg = result.scalars().first()

    if not cfg:
        cfg = TenantModelConfig(tenant_id=tenant_id)
        db.add(cfg)
        await db.flush()
        await db.refresh(cfg)

    update_data = body.model_dump(exclude_unset=True)

    # Convert string UUIDs to actual UUIDs for FK fields
    uuid_fields = [
        "manual_model_id", "manual_custom_model_id",
        "auto_light_model_id", "auto_heavy_model_id",
        "auto_light_custom_model_id", "auto_heavy_custom_model_id",
    ]
    for field in uuid_fields:
        if field in update_data:
            val = update_data[field]
            if val:
                update_data[field] = uuid.UUID(val)
            else:
                update_data[field] = None

    if "mode" in update_data:
        mode = update_data["mode"]
        if mode not in ("manual", "auto"):
            raise HTTPException(status_code=400, detail="mode must be 'manual' or 'auto'.")

    for field, value in update_data.items():
        setattr(cfg, field, value)

    await db.flush()
    await db.refresh(cfg)
    return await _config_to_response(cfg, db)
