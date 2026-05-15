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
    try:
        async with httpx.AsyncClient(timeout=settings.STT_TIMEOUT_SECONDS) as client:
            data = {"model": (None, settings.STT_MODEL), "response_format": (None, "json")}
            if language:
                data["language"] = (None, language)
            resp = await client.post(
                settings.STT_URL,
                files={"file": (fname, audio_bytes, mime), **data},
            )
            resp.raise_for_status()
            payload = resp.json()
            text = (payload.get("text") or "").strip()
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

    payload = {
        "model": settings.TTS_MODEL,
        "input": text,
        "voice": voice,
        "response_format": fmt,
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
