"""Voice IO for the tenant-facing chat: STT (mic → text) and TTS (text → audio).

Both endpoints are thin proxies on top of the OpenAI-compatible Whisper and
openedai-speech servers running on the GPU host. We keep the proxy in the
tenant scope (rather than a global global voice service) so:
  • the tenant API key is what authorizes voice access — same auth surface
    as the rest of the chat;
  • per-tenant rate limiting / disable is one place to add later;
  • the UI can hit `/api/tenants/{tid}/voice/...` from the same base URL.

The audio formats we accept are whatever MediaRecorder produces in the
browser (webm/opus is typical; Whisper handles it). The audio we return is
mp3 by default — small, browser-decoded everywhere.
"""
from __future__ import annotations

import io
import logging
import uuid

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.api.deps import TenantAuthContext, get_current_tenant_auth_context
from app.core.config import settings
from app.models.tenant import Tenant

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/tenants/{tenant_id}/voice",
    tags=["tenant-voice"],
)


def _verify_tenant(tenant_id: uuid.UUID, tenant: Tenant) -> None:
    if str(tenant.id) != str(tenant_id):
        raise HTTPException(status_code=403, detail="Forbidden")


class STTResponse(BaseModel):
    text: str


# Whisper large-v3 was trained on a lot of YouTube-subtitle data and falls
# back to common subtitle boilerplate when given silence, near-silence, or
# unparseable noise. These show up as "valid" transcripts and would otherwise
# get sent to the LLM as if the user said them. List grows over time as new
# hallucinations are spotted in the wild — match case-insensitively.
import re as _re
_HALLUCINATION_PATTERNS = [
    _re.compile(p, _re.IGNORECASE)
    for p in (
        r"\bсубтитры\s+(?:сделал|подготовил|создал|перевод)\b",
        r"\bdimatorzok\b",
        r"продолжение\s+следует",
        r"спасибо\s+за\s+просмотр",
        r"подпис(ывайтесь|ка)\s+на\s+канал",
        r"редактор\s+субтитров",
        r"корректор\s+(?:а\.|субтитров)",
        r"\bthanks?\s+for\s+watching\b",
        r"\bplease\s+(?:like\s+and\s+)?subscribe\b",
        r"\bcorrect(?:ion)?s?\s+by\b",
    )
]


def _is_hallucination(text: str) -> bool:
    """Cheap check: Whisper boilerplate fired on silence/noise. The model
    rarely produces these for real speech, so dropping them is safe."""
    if not text or len(text.strip()) < 2:
        return False
    return any(p.search(text) for p in _HALLUCINATION_PATTERNS)


@router.post("/stt", response_model=STTResponse)
async def speech_to_text(
    tenant_id: uuid.UUID,
    file: UploadFile = File(...),
    language: str | None = Form(default=None),
    auth: TenantAuthContext = Depends(get_current_tenant_auth_context),
) -> STTResponse:
    """Transcribe an audio blob to text. The browser sends what
    MediaRecorder produced (usually audio/webm). Whisper accepts it as-is."""
    _verify_tenant(tenant_id, auth.tenant)
    audio_bytes = await file.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio")
    if len(audio_bytes) > 25 * 1024 * 1024:  # 25 MB
        raise HTTPException(status_code=413, detail="Audio too large (max 25 MB)")

    fname = file.filename or "speech.webm"
    mime = file.content_type or "audio/webm"
    # Default to tenant-configured (or backend default) language if the
    # client didn't pass one. Whisper accuracy with a fixed language tag is
    # noticeably better than auto-detect on ru/uk technical content.
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
            payload = resp.json()
            text = (payload.get("text") or "").strip()
            if _is_hallucination(text):
                logger.info("STT dropped hallucination: %r", text[:200])
                return STTResponse(text="")
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
    # mp3 / wav / flac / opus / aac — passed to upstream as `response_format`.
    format: str = "mp3"
    # Playback speed multiplier. None → backend default (settings.TTS_SPEED).
    # OpenAI-spec range is 0.25..4.0; we clamp.
    speed: float | None = None


@router.post("/tts")
async def text_to_speech(
    tenant_id: uuid.UUID,
    body: TTSRequest,
    auth: TenantAuthContext = Depends(get_current_tenant_auth_context),
) -> StreamingResponse:
    """Synthesize the given text into an audio stream. The browser receives
    the raw audio bytes and plays them via `<audio>`."""
    _verify_tenant(tenant_id, auth.tenant)
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Empty text")
    if len(text) > 4000:
        # XTTS handles long inputs, but truncate to keep latency sane —
        # the UI is welcome to split into chunks if it wants the full reply.
        text = text[:4000]

    voice = body.voice or settings.TTS_VOICE
    fmt = (body.format or "mp3").lower()
    if fmt not in ("mp3", "wav", "flac", "opus", "aac"):
        fmt = "mp3"
    mime = {"mp3": "audio/mpeg", "wav": "audio/wav", "flac": "audio/flac",
            "opus": "audio/ogg", "aac": "audio/aac"}.get(fmt, "audio/mpeg")

    speed = body.speed if body.speed is not None else settings.TTS_SPEED
    # OpenAI-spec range 0.25..4.0; clamp.
    speed = max(0.25, min(4.0, float(speed)))
    payload = {
        "model": settings.TTS_MODEL,
        "input": text,
        "voice": voice,
        "response_format": fmt,
        "speed": speed,
    }
    try:
        # Stream the upstream response straight to the browser — no full
        # buffering in our process for long inputs.
        client = httpx.AsyncClient(timeout=settings.TTS_TIMEOUT_SECONDS)
        upstream = await client.send(
            client.build_request("POST", settings.TTS_URL, json=payload),
            stream=True,
        )
        upstream.raise_for_status()

        async def _gen():
            try:
                async for chunk in upstream.aiter_bytes():
                    yield chunk
            finally:
                await upstream.aclose()
                await client.aclose()

        return StreamingResponse(_gen(), media_type=mime)
    except httpx.HTTPStatusError as e:
        await client.aclose()
        logger.error("TTS HTTP %s: %s", e.response.status_code, (e.response.text or "")[:300])
        raise HTTPException(status_code=502, detail=f"TTS upstream error {e.response.status_code}")
    except Exception as e:
        try:
            await client.aclose()
        except Exception:
            pass
        logger.exception("TTS failed")
        raise HTTPException(status_code=502, detail=f"TTS failed: {str(e)[:200]}")
