from pydantic_settings import BaseSettings
from typing import List
import json


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://ai_platform:ai_platform_secret@localhost:5432/ai_platform"
    SECRET_KEY: str = "change-me"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480
    ENCRYPTION_KEY: str = "change-me"
    CORS_ORIGINS: str = '["http://localhost:5173"]'
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    ADMIN_LOGIN: str = "admin"
    ADMIN_PASSWORD: str = "admin"
    LOG_RETENTION_DAYS: int = 90
    ATTACHMENT_MAX_FILE_MB: int = 50  # per-file hard limit for chat attachments
    ATTACHMENT_DRAFT_TTL_HOURS: int = 24  # unsent drafts are GC'd after this
    # PaddleOCR-server on the GPU host. Empty → fall back to local Tesseract (CPU).
    # /v1/ocr/auto = dual-pass (cyrillic + en, per-bbox keep best confidence) —
    # fixes 6→б, 0→о substitutions on mixed RU/EN technical content.
    OCR_URL: str = "http://172.10.100.9:8003/v1/ocr/auto"
    OCR_TIMEOUT_SECONDS: float = 30.0
    # Voice in chat: STT (Whisper) and TTS (openedai-speech / XTTS-v2).
    # Both are OpenAI-compatible endpoints on the GPU host.
    STT_URL: str = "http://172.10.100.9:8001/v1/audio/transcriptions"
    STT_MODEL: str = "Systran/faster-whisper-large-v3"
    STT_TIMEOUT_SECONDS: float = 60.0
    TTS_URL: str = "http://172.10.100.9:8002/v1/audio/speech"
    TTS_MODEL: str = "tts-1-hd"
    TTS_VOICE: str = "alloy"
    TTS_TIMEOUT_SECONDS: float = 60.0
    # Default speech speed. 1.0 = XTTS-v2's natural pace (feels slow);
    # 1.15-1.25 sounds natural for ru/uk. Client can override per request.
    TTS_SPEED: float = 1.2
    # Default STT language code. Empty string = let Whisper auto-detect.
    # Fixing to "ru" significantly improves accuracy on ru/uk technical
    # content vs auto-detect. UI may override per request.
    STT_LANGUAGE: str = "ru"
    # PDF processing — if a page has fewer than this many characters in its
    # native text layer, treat it as a scan and render → OCR via OCR_URL.
    PDF_PAGE_TEXT_LAYER_MIN_CHARS: int = 50
    # Hard cap: don't OCR endless PDFs. 30 pages × ~1s/page on GPU ≈ 30s.
    PDF_OCR_MAX_PAGES: int = 30
    # Rasterization DPI for PDF→PNG conversion before OCR.
    PDF_OCR_RENDER_DPI: int = 200
    # CPU-vision (Ollama llava/minicpm) is OFF by default — it takes minutes per
    # image on CPU. Enable only on hosts with GPU vision.
    ENABLE_CPU_VISION_DESCRIPTION: bool = False

    @property
    def cors_origins_list(self) -> List[str]:
        return json.loads(self.CORS_ORIGINS)

    model_config = {"env_file": "../.env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
