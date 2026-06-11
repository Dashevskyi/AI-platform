"""Voice IO (admin mirror of tenant voice endpoints)."""
from __future__ import annotations

import logging
import re
import uuid

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_tenant_access
from app.core.config import settings
from app.core.ratelimit import voice_limiter
from app.core.database import get_db
from app.models.admin_user import AdminUser

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin/tenants/{tenant_id}/voice",
    tags=["admin-voice"],
    dependencies=[Depends(voice_limiter)],
)


class STTResponse(BaseModel):
    text: str


# Drop Whisper-large-v3 hallucinations on silence (it falls back to YouTube
# subtitle boilerplate). See app/api/tenant/voice.py for the rationale.
from app.api.tenant.voice import _is_hallucination  # noqa: E402
from app.services.stt_normalizer import fix_address_fractions as _fix_addr_fracs  # noqa: E402


@router.post("/stt", response_model=STTResponse)
async def speech_to_text_admin(
    tenant_id: uuid.UUID,
    file: UploadFile = File(...),
    language: str | None = Form(default=None),
    current_user: AdminUser = Depends(require_tenant_access),
) -> STTResponse:
    audio_bytes = await file.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio")
    if len(audio_bytes) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Audio too large (max 25 MB)")
    fname = file.filename or "speech.webm"
    mime = file.content_type or "audio/webm"
    effective_lang = (language or "").strip() or settings.STT_LANGUAGE
    try:
        async with httpx.AsyncClient(timeout=settings.STT_TIMEOUT_SECONDS) as client:
            data = {"model": (None, settings.STT_MODEL), "response_format": (None, "json")}
            if effective_lang:
                data["language"] = (None, effective_lang)
            resp = await client.post(
                settings.STT_URL,
                files={"file": (fname, audio_bytes, mime), **data},
            )
            resp.raise_for_status()
            text = (resp.json().get("text") or "").strip()
            if _is_hallucination(text):
                logger.info("STT dropped hallucination: %r", text[:200])
                return STTResponse(text="")
            if text:
                text = _fix_addr_fracs(text)
            return STTResponse(text=text)
    except httpx.HTTPStatusError as e:
        logger.error("STT HTTP %s: %s", e.response.status_code, (e.response.text or "")[:300])
        raise HTTPException(status_code=502, detail=f"STT upstream error {e.response.status_code}")
    except Exception as e:
        logger.exception("STT failed")
        raise HTTPException(status_code=502, detail=f"STT failed: {str(e)[:200]}")


class TTSRequest(BaseModel):
    text: str
    voice: str | None = None
    format: str = "mp3"
    speed: float | None = None


@router.post("/tts")
async def text_to_speech_admin(
    tenant_id: uuid.UUID,
    body: TTSRequest,
    current_user: AdminUser = Depends(require_tenant_access),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """Admin TTS — uses the same per-tenant provider resolution as the tenant endpoint.
    Reads tts_provider from shell config so admin test-voice reflects real settings."""
    from app.api.tenant.voice import (
        _sanitize_for_tts, _load_tts_config, _detect_lang, _split_tts_chunks,
        _normalize_numbers_for_silero,
    )

    text = _sanitize_for_tts((body.text or "").strip())
    if not text:
        raise HTTPException(status_code=400, detail="Empty text")
    if len(text) > 4000:
        text = text[:4000]

    tts_cfg = await _load_tts_config(tenant_id, db)

    # ── ElevenLabs ──────────────────────────────────────────────────────────
    if tts_cfg.provider == "elevenlabs":
        voice_id = tts_cfg.voice_id or settings.ELEVENLABS_VOICE_ID
        model = tts_cfg.model or settings.ELEVENLABS_MODEL
        el_url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"
        el_payload = {
            "text": text,
            "model_id": model,
            "voice_settings": {"stability": 0.45, "similarity_boost": 0.80, "style": 0.0, "use_speaker_boost": True},
        }
        el_headers = {"xi-api-key": tts_cfg.api_key, "Content-Type": "application/json", "Accept": "audio/mpeg"}

        async def _el_gen():
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    async with client.stream("POST", el_url, json=el_payload, headers=el_headers) as resp:
                        if resp.status_code != 200:
                            logger.error("ElevenLabs HTTP %s: %s", resp.status_code, (await resp.aread())[:300])
                            return
                        async for chunk in resp.aiter_bytes():
                            yield chunk
            except Exception as exc:
                logger.error("ElevenLabs TTS (admin) failed: %s", exc)

        return StreamingResponse(_el_gen(), media_type="audio/mpeg")

    # ── Silero ──────────────────────────────────────────────────────────────
    if tts_cfg.provider == "silero":
        silero_base = tts_cfg.fish_url or settings.SILERO_TTS_URL.rstrip("/")
        lang = _detect_lang(text)
        silero_lang = "ua" if lang == "uk" else "ru"
        # tts_voice_id is shared across providers — an ElevenLabs voice id may
        # linger here after a provider switch. Only accept silero-looking names.
        _vid = tts_cfg.voice_id or ""
        speaker = _vid if re.fullmatch(r"[a-z]+_[a-z0-9_]+", _vid) else (
            settings.SILERO_SPEAKER_UA if silero_lang == "ua" else settings.SILERO_SPEAKER_RU
        )
        text_silero = _normalize_numbers_for_silero(text, silero_lang)
        silero_payload = {"text": text_silero, "lang": silero_lang, "speaker": speaker, "sample_rate": 48000,
                          "speed": tts_cfg.speed or 1.0, "pitch": getattr(tts_cfg, "pitch", None)}
        logger.debug("TTS(admin): Silero %d chars, lang=%s, speaker=%s", len(text_silero), silero_lang, speaker)

        async def _silero_gen():
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.post(f"{silero_base}/tts", json=silero_payload)
                    if resp.status_code != 200:
                        logger.error("Silero HTTP %s: %s", resp.status_code, (resp.text or "")[:300])
                        return
                    yield resp.content
            except Exception as exc:
                logger.error("Silero TTS (admin) failed: %s", exc)

        return StreamingResponse(_silero_gen(), media_type="audio/wav")

    # ── Fish Speech ─────────────────────────────────────────────────────────
    import ormsgpack as _msgpack  # type: ignore[import]
    fish_base = tts_cfg.fish_url or settings.TTS_URL.rstrip("/")
    chunks = _split_tts_chunks(text)

    async def _fs_gen():
        async with httpx.AsyncClient(timeout=settings.TTS_TIMEOUT_SECONDS) as client:
            for i, chunk_text in enumerate(chunks):
                lang = _detect_lang(chunk_text)
                ref_id = settings.FISH_SPEECH_REF_UK if lang == "uk" else settings.FISH_SPEECH_REF_RU
                body_bytes = _msgpack.packb({
                    "text": chunk_text, "reference_id": ref_id,
                    "format": "mp3", "normalize": True, "streaming": False, "use_memory_cache": "on",
                })
                try:
                    async with client.stream(
                        "POST", f"{fish_base}/v1/tts", content=body_bytes,
                        headers={"Content-Type": "application/msgpack"},
                    ) as upstream:
                        upstream.raise_for_status()
                        async for data in upstream.aiter_bytes():
                            yield data
                except Exception as exc:
                    logger.error("TTS chunk %d failed: %s", i, exc)
                    return

    return StreamingResponse(_fs_gen(), media_type="audio/mpeg")
