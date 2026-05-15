"""
Resolves which LLM model + provider to use for a given tenant and request.

Supports:
- Manual mode: use the explicitly selected model (global or custom)
- Auto mode: classify query complexity, then pick light or heavy model
- Fallback: if no model config exists, fall back to shell_config fields (backward compat)
"""
import json
import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.llm_model import LLMModel
from app.models.tenant_custom_model import TenantCustomModel
from app.models.tenant_model_config import TenantModelConfig
from app.models.tenant_shell_config import TenantShellConfig
from app.providers.factory import get_provider
from app.providers.base import BaseProvider
from app.core.security import decrypt_value

logger = logging.getLogger(__name__)


@dataclass
class ResolvedModel:
    """Result of model resolution: everything needed to call the provider."""
    provider: BaseProvider
    provider_type: str
    model_name: str
    supports_tools: bool
    supports_vision: bool
    source: str  # "catalog", "custom", "shell_config"


COMPLEXITY_PROMPT = """Rate the complexity of this user query on a scale from 0.0 to 1.0.

0.0 = trivial (greetings, simple factual questions, translations, one-line tasks)
0.5 = moderate (multi-step reasoning, summaries, code explanations)
1.0 = complex (code generation, analysis of large data, multi-domain reasoning, creative writing)

Respond with ONLY a number between 0.0 and 1.0, nothing else.

Query: {query}"""


async def _classify_complexity(
    provider: BaseProvider,
    model_name: str,
    user_content: str,
) -> float:
    """Use a lightweight LLM call to classify query complexity (0.0 - 1.0)."""
    try:
        prompt = COMPLEXITY_PROMPT.format(query=user_content[:500])
        resp = await provider.chat_completion(
            messages=[{"role": "user", "content": prompt}],
            model=model_name,
            temperature=0.0,
            max_tokens=10,
        )
        text = resp.content.strip()
        # Extract the first float-like value
        for token in text.split():
            try:
                val = float(token)
                return max(0.0, min(1.0, val))
            except ValueError:
                continue
        return 0.5
    except Exception as e:
        logger.warning(f"Complexity classification failed: {e}")
        return 0.5


async def _load_model_record(
    model_id, custom_model_id, db: AsyncSession
) -> tuple:
    """Load a model from either the global catalog or tenant custom models.
    Returns (model_record, is_custom).
    """
    if model_id:
        result = await db.execute(
            select(LLMModel).where(LLMModel.id == model_id, LLMModel.is_active == True)  # noqa: E712
        )
        m = result.scalars().first()
        if m:
            return m, False

    if custom_model_id:
        result = await db.execute(
            select(TenantCustomModel).where(
                TenantCustomModel.id == custom_model_id,
                TenantCustomModel.is_active == True,  # noqa: E712
                TenantCustomModel.deleted_at.is_(None),
            )
        )
        m = result.scalars().first()
        if m:
            return m, True

    return None, False


def _make_provider(record, is_custom: bool) -> ResolvedModel:
    """Create a ResolvedModel from a catalog or custom model record."""
    api_key = None
    if record.api_key_enc:
        api_key = decrypt_value(record.api_key_enc)

    provider = get_provider(record.provider_type, record.base_url, api_key)

    return ResolvedModel(
        provider=provider,
        provider_type=record.provider_type,
        model_name=record.model_id,
        supports_tools=record.supports_tools,
        supports_vision=record.supports_vision,
        source="custom" if is_custom else "catalog",
    )


async def resolve_model(
    tenant_id: str,
    user_content: str,
    db: AsyncSession,
    shell_config: TenantShellConfig,
) -> ResolvedModel:
    """
    Resolve which model to use for a tenant's request.

    Priority:
    1. TenantModelConfig (if exists) — manual or auto mode
    2. Fallback to shell_config provider/model fields (backward compat)
    """
    # Try to load tenant model config
    result = await db.execute(
        select(TenantModelConfig).where(TenantModelConfig.tenant_id == tenant_id)
    )
    model_config = result.scalars().first()

    if not model_config:
        # Fallback to legacy shell_config
        return _resolve_from_shell_config(shell_config)

    if model_config.mode == "manual":
        record, is_custom = await _load_model_record(
            model_config.manual_model_id,
            model_config.manual_custom_model_id,
            db,
        )
        if record:
            return _make_provider(record, is_custom)
        # No model configured yet — fall back
        return _resolve_from_shell_config(shell_config)

    if model_config.mode == "auto":
        return await _resolve_auto(model_config, user_content, db, shell_config)

    # Unknown mode — fallback
    return _resolve_from_shell_config(shell_config)


async def _resolve_auto(
    model_config: TenantModelConfig,
    user_content: str,
    db: AsyncSession,
    shell_config: TenantShellConfig,
) -> ResolvedModel:
    """Auto mode: classify complexity, then pick light or heavy model."""
    # Load the light model (used for classification AND for simple queries)
    light_record, light_custom = await _load_model_record(
        model_config.auto_light_model_id,
        model_config.auto_light_custom_model_id,
        db,
    )
    heavy_record, heavy_custom = await _load_model_record(
        model_config.auto_heavy_model_id,
        model_config.auto_heavy_custom_model_id,
        db,
    )

    if not light_record and not heavy_record:
        return _resolve_from_shell_config(shell_config)

    # If only one is configured, use it for everything
    if not light_record:
        return _make_provider(heavy_record, heavy_custom)
    if not heavy_record:
        return _make_provider(light_record, light_custom)

    # Both configured — classify complexity using the light model
    light_resolved = _make_provider(light_record, light_custom)
    complexity = await _classify_complexity(
        light_resolved.provider,
        light_resolved.model_name,
        user_content,
    )

    logger.debug(f"Auto model selection: complexity={complexity:.2f}, threshold={model_config.complexity_threshold}")

    if complexity < model_config.complexity_threshold:
        return light_resolved
    else:
        return _make_provider(heavy_record, heavy_custom)


def _resolve_from_shell_config(config: TenantShellConfig) -> ResolvedModel:
    """Legacy fallback: build ResolvedModel from shell_config fields."""
    api_key = None
    if config.provider_api_key_enc:
        api_key = decrypt_value(config.provider_api_key_enc)

    provider = get_provider(config.provider_type, config.provider_base_url, api_key)

    return ResolvedModel(
        provider=provider,
        provider_type=config.provider_type,
        model_name=config.model_name.strip(),
        supports_tools=True,
        supports_vision=False,
        source="shell_config",
    )
