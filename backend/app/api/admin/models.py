"""
Admin endpoints for global LLM model catalog.
"""
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import encrypt_value, decrypt_value, mask_secret
from app.models.llm_model import LLMModel
from app.schemas.llm_model import (
    LLMModelCreate,
    LLMModelUpdate,
    LLMModelResponse,
    LLMModelBrief,
    TestModelConnectionRequest,
    TestModelConnectionResponse,
)
from app.schemas.common import PaginatedResponse
from app.api.deps import require_role

router = APIRouter(
    prefix="/api/admin/models",
    tags=["admin-models"],
    dependencies=[Depends(require_role("superadmin", "tenant_admin"))],
)


def _model_to_response(m: LLMModel) -> LLMModelResponse:
    masked_key: str | None = None
    if m.api_key_enc:
        try:
            raw = decrypt_value(m.api_key_enc)
            masked_key = mask_secret(raw)
        except Exception:
            masked_key = "****"

    return LLMModelResponse(
        id=str(m.id),
        name=m.name,
        provider_type=m.provider_type,
        base_url=m.base_url,
        api_key_masked=masked_key,
        model_id=m.model_id,
        tier=m.tier,
        supports_tools=m.supports_tools,
        supports_vision=m.supports_vision,
        max_context_tokens=m.max_context_tokens,
        cost_per_1k_input=m.cost_per_1k_input,
        cost_per_1k_output=m.cost_per_1k_output,
        is_active=m.is_active,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


@router.get("/", response_model=PaginatedResponse[LLMModelResponse])
async def list_models(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    is_active: bool | None = None,
    tier: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    query = select(LLMModel)
    if is_active is not None:
        query = query.where(LLMModel.is_active == is_active)
    if tier:
        query = query.where(LLMModel.tier == tier)
    query = query.order_by(LLMModel.name)

    count_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar()

    items = (
        await db.execute(query.offset((page - 1) * page_size).limit(page_size))
    ).scalars().all()

    return PaginatedResponse[LLMModelResponse](
        items=[_model_to_response(m) for m in items],
        total_count=total,
        page=page,
        page_size=page_size,
    )


@router.get("/brief", response_model=list[LLMModelBrief])
async def list_models_brief(
    db: AsyncSession = Depends(get_db),
):
    """Short list of active models for selection dropdowns."""
    result = await db.execute(
        select(LLMModel)
        .where(LLMModel.is_active == True)  # noqa: E712
        .order_by(LLMModel.name)
    )
    models = result.scalars().all()
    return [
        LLMModelBrief(
            id=str(m.id),
            name=m.name,
            provider_type=m.provider_type,
            model_id=m.model_id,
            tier=m.tier,
            supports_tools=m.supports_tools,
            supports_vision=m.supports_vision,
        )
        for m in models
    ]


@router.get("/{model_id}", response_model=LLMModelResponse)
async def get_model(
    model_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(LLMModel).where(LLMModel.id == model_id))
    m = result.scalars().first()
    if not m:
        raise HTTPException(status_code=404, detail="Model not found.")
    return _model_to_response(m)


@router.post("/", response_model=LLMModelResponse, status_code=status.HTTP_201_CREATED)
async def create_model(
    body: LLMModelCreate,
    db: AsyncSession = Depends(get_db),
):
    m = LLMModel(
        name=body.name,
        provider_type=body.provider_type,
        base_url=body.base_url,
        model_id=body.model_id,
        tier=body.tier,
        supports_tools=body.supports_tools,
        supports_vision=body.supports_vision,
        max_context_tokens=body.max_context_tokens,
        cost_per_1k_input=body.cost_per_1k_input,
        cost_per_1k_output=body.cost_per_1k_output,
        is_active=body.is_active,
    )
    if body.api_key:
        m.api_key_enc = encrypt_value(body.api_key)

    db.add(m)
    await db.flush()
    await db.refresh(m)
    return _model_to_response(m)


@router.patch("/{model_id}", response_model=LLMModelResponse)
async def update_model(
    model_id: uuid.UUID,
    body: LLMModelUpdate,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(LLMModel).where(LLMModel.id == model_id))
    m = result.scalars().first()
    if not m:
        raise HTTPException(status_code=404, detail="Model not found.")

    update_data = body.model_dump(exclude_unset=True)

    if "api_key" in update_data:
        raw_key = update_data.pop("api_key")
        if raw_key:
            m.api_key_enc = encrypt_value(raw_key)
        else:
            m.api_key_enc = None

    for field, value in update_data.items():
        setattr(m, field, value)

    await db.flush()
    await db.refresh(m)
    return _model_to_response(m)


@router.delete("/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_model(
    model_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(LLMModel).where(LLMModel.id == model_id))
    m = result.scalars().first()
    if not m:
        raise HTTPException(status_code=404, detail="Model not found.")
    await db.delete(m)
    await db.flush()


@router.post("/test-connection", response_model=TestModelConnectionResponse)
async def test_model_connection(
    body: TestModelConnectionRequest,
):
    base_url = (body.base_url or "").rstrip("/")

    if not base_url:
        if body.provider_type == "ollama":
            base_url = "http://localhost:11434"
        else:
            return TestModelConnectionResponse(success=False, message="URL провайдера не указан.")

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            if body.provider_type == "ollama":
                resp = await client.get(f"{base_url}/api/tags")
                resp.raise_for_status()
                data = resp.json()
                models = [m.get("name", "") for m in data.get("models", [])]
                return TestModelConnectionResponse(
                    success=True,
                    message=f"Подключено к Ollama. Найдено моделей: {len(models)}.",
                    models=models,
                )
            else:
                headers = {}
                if body.api_key:
                    headers["Authorization"] = f"Bearer {body.api_key}"
                resp = await client.get(f"{base_url}/models", headers=headers)
                resp.raise_for_status()
                data = resp.json()
                models_list = data.get("data", [])
                models = [m.get("id", "") for m in models_list]
                return TestModelConnectionResponse(
                    success=True,
                    message=f"Подключено. Найдено моделей: {len(models)}.",
                    models=models,
                )
    except httpx.HTTPStatusError as exc:
        return TestModelConnectionResponse(
            success=False,
            message=f"Ошибка HTTP {exc.response.status_code}: {exc.response.text[:300]}",
        )
    except Exception as exc:
        return TestModelConnectionResponse(
            success=False,
            message=f"Ошибка соединения: {str(exc)[:300]}",
        )
