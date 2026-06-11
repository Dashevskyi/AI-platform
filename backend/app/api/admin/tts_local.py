"""Admin proxy for the local TTS service (silero-v5) settings.

The local TTS service keeps system-wide pronunciation rules (abbreviation
expansions, hard-э respellings, stress overrides) in its own /rules API.
These are NOT per-tenant settings — they shape the voice for every tenant
using the local provider — so this proxy is superadmin-only.
"""
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from app.api.deps import require_role
from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin/tts-local",
    tags=["admin-tts-local"],
    dependencies=[Depends(require_role("superadmin"))],
)


def _base() -> str:
    return settings.SILERO_TTS_URL.rstrip("/")


class RulesPayload(BaseModel):
    abbr: dict[str, str] = {}
    pron: dict[str, str] = {}
    stress: dict[str, str] = {}


class TTSTestPayload(BaseModel):
    text: str
    lang: str = "ru"
    speaker: str | None = None
    speed: float | None = None
    pitch: str | None = None


@router.get("/rules")
async def get_rules() -> dict:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{_base()}/rules")
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        logger.error("tts-local rules fetch failed: %s", exc)
        raise HTTPException(502, f"Локальный TTS недоступен: {exc}")


@router.put("/rules")
async def put_rules(body: RulesPayload) -> dict:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.put(f"{_base()}/rules", json=body.model_dump())
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(exc.response.status_code, (exc.response.text or "")[:300])
    except Exception as exc:
        logger.error("tts-local rules save failed: %s", exc)
        raise HTTPException(502, f"Локальный TTS недоступен: {exc}")


@router.get("/speakers")
async def get_speakers() -> dict:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{_base()}/speakers")
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        raise HTTPException(502, f"Локальный TTS недоступен: {exc}")


@router.post("/test")
async def tts_test(body: TTSTestPayload) -> Response:
    """Synthesize a test phrase straight on the local service (bypasses tenant
    provider settings — this page configures the LOCAL engine specifically)."""
    if not body.text.strip():
        raise HTTPException(400, "Пустой текст")
    payload = {
        "text": body.text,
        "lang": body.lang,
        "format": "mp3",
        **({"speaker": body.speaker} if body.speaker else {}),
        **({"speed": body.speed} if body.speed else {}),
        **({"pitch": body.pitch} if body.pitch else {}),
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(f"{_base()}/tts", json=payload)
            resp.raise_for_status()
            return Response(content=resp.content, media_type="audio/mpeg")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(exc.response.status_code, (exc.response.text or "")[:300])
    except Exception as exc:
        raise HTTPException(502, f"Локальный TTS недоступен: {exc}")
