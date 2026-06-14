"""
Resolves which LLM model + provider to use for a given tenant and request.

Supports:
- Manual mode: use the explicitly selected model (global or custom)
- Auto mode: classify query complexity, then pick light or heavy model
- Fallback: if no model config exists, fall back to shell_config fields (backward compat)
"""
import json
import logging
import re
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
    max_context_tokens: int | None = None
    # When set, pipeline re-evaluates router state per round (size-based
    # escalation). Stays None for manual mode / single-model auto.
    auto_router: "AutoRouter | None" = None


@dataclass
class AutoRouter:
    """Per-request routing state for auto-mode. Pipeline calls `pick` before
    each LLM round; size-based escalation is deterministic (no extra LLM
    call); classifier (legacy) runs at most once. One-way: once we go heavy
    for this request we stay heavy (avoids ping-pong + tone drift)."""
    light: ResolvedModel
    heavy: ResolvedModel | None
    size_threshold: int                 # tokens; 0 = size routing off
    use_classifier: bool
    complexity_threshold: float
    user_content: str                   # cached for classifier
    escalated: bool = False
    classifier_done: bool = False
    # When True, ALL escalation paths are blocked — even huge contexts and
    # high-complexity classifications keep using `light`. Set by the pipeline
    # when PII routing detects sensitive entities (phone/MAC/IP) in the user
    # query and the tenant has `pii_routing_enabled`. Trades reasoning quality
    # for guaranteed data residency.
    pii_locked: bool = False
    pii_lock_reason: str | None = None  # e.g. "phone in user query"

    async def pick(self, tokens_estimate: int) -> tuple[ResolvedModel, str]:
        """Return (chosen_model, reason). Reason goes to logs/debug."""
        if not self.heavy:
            return self.light, "single-model"
        if self.pii_locked:
            return self.light, f"PII-locked: {self.pii_lock_reason or 'PII detected'}"
        if self.escalated:
            return self.heavy, "escalated (sticky heavy)"
        if self.size_threshold > 0 and tokens_estimate > self.size_threshold:
            self.escalated = True
            return self.heavy, f"size {tokens_estimate} > {self.size_threshold}"
        if self.use_classifier and not self.classifier_done:
            self.classifier_done = True
            complexity = await _classify_complexity(
                self.light.provider, self.light.model_name, self.user_content,
            )
            if complexity >= self.complexity_threshold:
                self.escalated = True
                return self.heavy, f"classifier {complexity:.2f} >= {self.complexity_threshold:.2f}"
            return self.light, f"classifier {complexity:.2f} < {self.complexity_threshold:.2f}"
        return self.light, "default light"


COMPLEXITY_PROMPT = """Rate the complexity of this user query on a scale from 0.0 to 1.0.

0.0 = trivial: greetings, simple arithmetic (e.g. "2+2"), one-word/one-line
      factual questions, translations of short phrases, yes/no checks
0.3 = light: short factual questions needing one tool call or one paragraph
0.5 = moderate: multi-step reasoning, summaries, code explanations
0.8 = heavy: code generation, debugging, multi-source analysis
1.0 = complex: long creative writing, deep multi-domain reasoning, large data
      analysis

Respond with ONLY a single number between 0.0 and 1.0, nothing else, no words.

Query: {query}"""

_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _parse_score(text: str) -> float | None:
    if not text:
        return None
    m = _NUMBER_RE.search(text)
    if not m:
        return None
    try:
        return max(0.0, min(1.0, float(m.group(0))))
    except ValueError:
        return None


async def _classify_complexity(
    provider: BaseProvider,
    model_name: str,
    user_content: str,
) -> float:
    """Use a lightweight LLM call to classify query complexity (0.0 - 1.0).

    Thinking is disabled (chat_template_kwargs.enable_thinking=false) — the
    classifier just needs to emit a number, reasoning here would (a) burn
    tokens and (b) push the number into the reasoning channel, leaving
    content empty and the parser unable to recover a score.
    """
    try:
        prompt = COMPLEXITY_PROMPT.format(query=user_content[:500])
        resp = await provider.chat_completion(
            messages=[{"role": "user", "content": prompt}],
            model=model_name,
            temperature=0.0,
            max_tokens=10,
            extra_body={"chat_template_kwargs": {"enable_thinking": False, "thinking": False}},
        )
        score = _parse_score((resp.content or "").strip())
        if score is None:
            # Defensive: some models still emit to reasoning even when asked
            # not to; recover the number from there before giving up.
            score = _parse_score((getattr(resp, "reasoning", None) or "").strip())
        if score is None:
            logger.warning(
                "Complexity classifier returned no number; content=%r reasoning=%r — defaulting to 0.5",
                (resp.content or "")[:80],
                (getattr(resp, "reasoning", None) or "")[:80],
            )
            return 0.5
        logger.debug("Complexity classifier returned %.2f for %r", score, user_content[:60])
        return score
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
        max_context_tokens=getattr(record, "max_context_tokens", None),
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
    # 0. Assistant model override (Assistant layer): if the request's assistant
    #    pins a specific model, it wins over the tenant's TenantModelConfig.
    _amid = getattr(shell_config, "assistant_model_id", None)
    if _amid:
        try:
            record, is_custom = await _load_model_record(_amid, None, db)
            if record:
                return _make_provider(record, is_custom)
        except Exception:
            logger.warning("assistant model override %s failed; falling back", _amid)

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
    """Auto mode: returns a ResolvedModel whose `auto_router` lets the
    pipeline re-decide per round based on actual context size.

    Initial pick is the light model (the cheap one). Pipeline will swap to
    heavy on the first round whose estimated tokens cross the size threshold
    (or, if the legacy classifier is enabled, on the first round if the
    classifier says so)."""
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
    if not light_record:
        return _make_provider(heavy_record, heavy_custom)
    if not heavy_record:
        return _make_provider(light_record, light_custom)

    light_resolved = _make_provider(light_record, light_custom)
    heavy_resolved = _make_provider(heavy_record, heavy_custom)

    router = AutoRouter(
        light=light_resolved,
        heavy=heavy_resolved,
        size_threshold=int(getattr(model_config, "auto_size_threshold", 24000) or 0),
        use_classifier=bool(getattr(model_config, "use_complexity_classifier", False)),
        complexity_threshold=float(getattr(model_config, "complexity_threshold", 0.5) or 0.5),
        user_content=user_content,
    )
    light_resolved.auto_router = router
    return light_resolved


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
