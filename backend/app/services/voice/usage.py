"""Voice entitlements (licensing gate) + usage metering for STT/TTS.

- `require_voice_entitlement` enforces the per-tenant stt_enabled/tts_enabled
  flag → HTTP 403 if the tenant isn't licensed for that service.
- `record_voice_usage` writes one VoiceUsage row (best-effort; never fails the
  user request). Units: chars for TTS, seconds for STT. Cost is filled when a
  per-1000-unit rate is configured (env VOICE_RATE_TTS_PER_1K_CHARS /
  VOICE_RATE_STT_PER_1K_SEC), else left NULL (volume tracked, priced later).
"""
import logging
import os
import uuid

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.voice_usage import VoiceUsage

logger = logging.getLogger(__name__)

_SERVICE_LABEL = {"stt": "распознавание речи (STT)", "tts": "синтез речи (TTS)"}


def require_voice_entitlement(tenant, kind: str) -> None:
    """Raise 403 if the tenant isn't entitled to this voice service."""
    flag = f"{kind}_enabled"
    if not bool(getattr(tenant, flag, True)):
        raise HTTPException(
            status_code=403,
            detail=f"Сервис «{_SERVICE_LABEL.get(kind, kind)}» не подключён для этого тенанта.",
        )


def _rate_per_unit(kind: str, unit_type: str) -> float:
    """Per-unit price (USD), derived from a per-1000-unit env rate. 0 → no cost."""
    if kind == "tts" and unit_type == "chars":
        return float(os.getenv("VOICE_RATE_TTS_PER_1K_CHARS", "0") or 0) / 1000.0
    if kind == "stt" and unit_type == "seconds":
        return float(os.getenv("VOICE_RATE_STT_PER_1K_SEC", "0") or 0) / 1000.0
    return 0.0


async def record_voice_usage(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    kind: str,                 # 'stt' | 'tts'
    units: int,
    unit_type: str,            # 'chars' | 'seconds'
    provider: str | None = None,
    api_key_id=None,
    chat_id=None,
    assistant_id=None,
    success: bool = True,
) -> None:
    """Insert one metering row. Best-effort — logs and swallows errors so a
    metering hiccup never breaks the actual STT/TTS response."""
    try:
        def _uid(v):
            if v is None:
                return None
            try:
                return v if isinstance(v, uuid.UUID) else uuid.UUID(str(v))
            except (ValueError, TypeError):
                return None

        rate = _rate_per_unit(kind, unit_type)
        cost = round(units * rate, 6) if (rate and units) else None
        db.add(VoiceUsage(
            tenant_id=tenant_id,
            api_key_id=_uid(api_key_id),
            assistant_id=_uid(assistant_id),
            chat_id=_uid(chat_id),
            kind=kind,
            provider=(provider or None),
            units=int(units or 0),
            unit_type=unit_type,
            cost_usd=cost,
            success=success,
        ))
        await db.flush()
    except Exception:
        logger.warning("voice usage metering failed (kind=%s) — non-fatal", kind, exc_info=True)
