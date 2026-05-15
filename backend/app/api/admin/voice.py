"""Voice IO (admin mirror of tenant voice endpoints)."""
from __future__ import annotations

import logging
import uuid

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.api.deps import require_role
from app.core.config import settings
from app.models.admin_user import AdminUser

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin/tenants/{tenant_id}/voice",
    tags=["admin-voice"],
)


class STTResponse(BaseModel):
    text: str


@router.post("/stt", response_model=STTResponse)
async def speech_to_text_admin(
    tenant_id: uuid.UUID,
    file: UploadFile = File(...),
    language: str | None = Form(default=None),
    current_user: AdminUser = Depends(require_role("superadmin", "tenant_admin")),
) -> STTResponse:
    audio_bytes = await file.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio")
    if len(audio_bytes) > 25 * 1024 * 1024:
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
            return STTResponse(text=(resp.json().get("text") or "").strip())
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


@router.post("/tts")
async def text_to_speech_admin(
    tenant_id: uuid.UUID,
    body: TTSRequest,
    current_user: AdminUser = Depends(require_role("superadmin", "tenant_admin")),
) -> StreamingResponse:
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Empty text")
    if len(text) > 4000:
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
