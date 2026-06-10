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
    # Embedding vector dimension. Fixed system-wide so pgvector can index the
    # embedding columns (see migration embdim01). bge-m3 = 1024; any configured
    # embedding model must produce this many dimensions.
    EMBEDDING_DIM: int = 1024
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
    STT_MODEL: str = "/model-turbo"
    STT_TIMEOUT_SECONDS: float = 60.0
    # Fish Speech 1.5 API base URL (MsgPack, /v1/tts endpoint).
    # Reference IDs map to voice-refs/<id>/ folders on the TTS server.
    # ru → Russian voice clone, uk → Ukrainian voice clone.
    TTS_URL: str = "http://172.10.100.9:8002"
    TTS_MODEL: str = "fish-speech-1.5"
    TTS_VOICE: str = "alloy"
    TTS_TIMEOUT_SECONDS: float = 60.0
    # Default speech speed. 1.0 = natural pace; 1.15-1.25 sounds natural for ru/uk.
    # Client can override per request.
    TTS_SPEED: float = 1.2
    # Fish Speech 1.5 reference_id for language-specific voice cloning.
    # These are folder names under voice-refs/ on the GPU host.
    FISH_SPEECH_REF_RU: str = "ru"
    FISH_SPEECH_REF_UK: str = "uk"
    # ── Silero TTS v4 (local, GPU, very fast ~0.1-1.5s per request) ─────────────
    # Lightweight FastAPI wrapper on top of snakers4/silero-models v4.
    # API: POST /tts {"text","lang":"ru"|"ua","speaker","sample_rate":24000} → WAV
    #      GET  /speakers → {"ru":[...],"ua":[...]}
    # Default speakers chosen for ISP support contexts (neutral, clear voice).
    SILERO_TTS_URL: str = "http://172.10.100.9:8004"
    SILERO_SPEAKER_RU: str = "xenia"   # ru speakers: aidar/baya/kseniya/xenia/eugene
    SILERO_SPEAKER_UA: str = "mykyta"  # ua speakers: mykyta/olena/lada/dobrynyla
    # ── ElevenLabs TTS (optional, overrides local TTS when set) ──────────────
    # Set ELEVENLABS_API_KEY in .env to enable. Falls back to local XTTS if empty.
    # model: eleven_turbo_v2_5 (fastest, multilingual) or eleven_multilingual_v2
    ELEVENLABS_API_KEY: str = ""
    ELEVENLABS_VOICE_ID: str = "21m00Tcm4TlvDq8ikWAM"  # Rachel — multilingual ru/uk
    ELEVENLABS_MODEL: str = "eleven_turbo_v2_5"
    # Default STT language code. Empty string = let Whisper auto-detect.
    # Fixing to "ru" significantly improves accuracy on ru/uk technical
    # content vs auto-detect. UI may override per request.
    STT_LANGUAGE: str = "ru"
    # WhisperLive streaming STT WebSocket (collabora/whisperlive-gpu).
    # Used as a backend proxy target for ws://.../voice/stt-stream.
    WHISPER_LIVE_WS_URL: str = "ws://172.10.100.9:9091"
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
