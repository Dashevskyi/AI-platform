"""
Admin endpoints for global LLM model catalog.
"""
import uuid
from datetime import datetime

import httpx
from pydantic import BaseModel
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
        last_check_at=m.last_check_at,
        last_check_status=m.last_check_status,
        last_check_detail=m.last_check_detail,
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


class ModelHealthCheckResponse(BaseModel):
    status: str          # ok | empty_content | no_completion | http_error | timeout | provider_error
    detail: str | None
    content: str | None  # what the model actually replied (first 200 chars)
    completion_tokens: int | None
    latency_ms: int | None
    checked_at: datetime


@router.post("/{model_id}/test", response_model=ModelHealthCheckResponse)
async def health_check_model(
    model_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Send a tiny probe to the model and record whether it actually
    answered. Catches the 'wrong model_id, provider returns empty content,
    pipeline silently degrades' failure mode that bit us earlier today."""
    import time
    from app.core.security import decrypt_value
    from app.providers.factory import get_provider
    from datetime import datetime, timezone as _tz

    m = (await db.execute(select(LLMModel).where(LLMModel.id == model_id))).scalars().first()
    if not m:
        raise HTTPException(status_code=404, detail="Model not found.")

    api_key = decrypt_value(m.api_key_enc) if m.api_key_enc else None
    status_code = "ok"
    detail: str | None = None
    content: str | None = None
    completion_tokens: int | None = None
    latency_ms: int | None = None

    try:
        provider = get_provider(m.provider_type, m.base_url, api_key)
        t0 = time.time()
        # Reasoning models (Qwen3-thinking, DeepSeek-R1, etc.) need headroom
        # to finish reasoning AND produce a short final answer; 20 is not enough.
        resp = await provider.chat_completion(
            messages=[{"role": "user", "content": "скажи привет одним словом"}],
            model=m.model_id,
            temperature=0.1,
            max_tokens=300,
        )
        latency_ms = int((time.time() - t0) * 1000)
        content = (resp.content or "")[:200]
        reasoning = (getattr(resp, "reasoning", None) or "")[:200]
        completion_tokens = int(resp.completion_tokens or 0)
        # Accept either content OR reasoning (the model IS replying, just via
        # the reasoning channel) — for reasoning models that's a healthy signal.
        if not (resp.content or "").strip() and not reasoning.strip():
            status_code = "empty_content"
            detail = (
                "Провайдер ответил, но и content и reasoning пустые. "
                "Скорее всего model_id не существует или есть отдельный "
                "канал который мы не парсим."
            )
        elif not (resp.content or "").strip() and reasoning.strip():
            # Reasoning came through but final answer didn't — common for R1/Qwen3
            # на короткий max_tokens; либо модель действительно ответила в reasoning.
            status_code = "ok"
            detail = "Содержимое в канале reasoning (reasoning-модель отвечает рассуждением)."
        elif completion_tokens == 0:
            status_code = "no_completion"
            detail = "completion_tokens=0 — usage не вернулся, но content есть."
    except httpx.HTTPStatusError as exc:
        status_code = "http_error"
        detail = f"HTTP {exc.response.status_code}: {(exc.response.text or '')[:300]}"
    except httpx.TimeoutException:
        status_code = "timeout"
        detail = "Запрос превысил таймаут."
    except Exception as exc:
        status_code = "provider_error"
        detail = f"{type(exc).__name__}: {str(exc)[:300]}"

    now = datetime.now(_tz.utc)
    m.last_check_at = now
    m.last_check_status = status_code
    m.last_check_detail = detail
    await db.commit()

    return ModelHealthCheckResponse(
        status=status_code,
        detail=detail,
        content=content,
        completion_tokens=completion_tokens,
        latency_ms=latency_ms,
        checked_at=now,
    )
