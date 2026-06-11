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

import asyncio
import io
import logging
import re
import uuid

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.api.deps import TenantAuthContext, get_current_tenant_auth_context
from app.core.config import settings
from app.core.ratelimit import voice_limiter
from app.core.database import get_db
from app.core.security import decrypt_value
from app.models.tenant import Tenant
from app.models.tenant_shell_config import TenantShellConfig
from app.services.stt_normalizer import get_tenant_vocab, normalize_transcript, invalidate_vocab_cache, fix_address_fractions
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/tenants/{tenant_id}/voice",
    tags=["tenant-voice"],
)


def _verify_tenant(tenant_id: uuid.UUID, tenant: Tenant) -> None:
    if str(tenant.id) != str(tenant_id):
        raise HTTPException(status_code=403, detail="Forbidden")


class _STTConfig:
    __slots__ = ("initial_prompt", "hotwords", "vocab_source", "vocab_dsn_enc", "fuzzy_threshold")

    def __init__(self, initial_prompt, hotwords, vocab_source, vocab_dsn_enc, fuzzy_threshold):
        self.initial_prompt = initial_prompt
        self.hotwords = hotwords
        self.vocab_source = vocab_source
        self.vocab_dsn_enc = vocab_dsn_enc
        self.fuzzy_threshold = fuzzy_threshold


async def _load_stt_config(tenant_id: uuid.UUID, db: AsyncSession) -> _STTConfig:
    """Load all STT-related settings from tenant shell config."""
    try:
        result = await db.execute(
            select(
                TenantShellConfig.stt_initial_prompt,
                TenantShellConfig.stt_hotwords,
                TenantShellConfig.stt_vocab_source,
                TenantShellConfig.stt_vocab_source_dsn_enc,
                TenantShellConfig.stt_fuzzy_threshold,
            ).where(TenantShellConfig.tenant_id == tenant_id)
        )
        row = result.first()
        if row:
            return _STTConfig(
                initial_prompt=row.stt_initial_prompt,
                hotwords=row.stt_hotwords,
                vocab_source=row.stt_vocab_source,
                vocab_dsn_enc=row.stt_vocab_source_dsn_enc,
                fuzzy_threshold=row.stt_fuzzy_threshold or 85.0,
            )
    except Exception as e:
        logger.warning("Failed to load STT config for tenant %s: %s", tenant_id, e)
    return _STTConfig(None, None, None, None, 85.0)


class _TTSConfig:
    """Resolved per-tenant TTS parameters (after fallback to system defaults)."""
    __slots__ = ("provider", "api_key", "voice_id", "model", "speed", "fish_url", "pitch")

    def __init__(self, provider, api_key, voice_id, model, speed, fish_url, pitch=None):
        self.provider = provider      # 'elevenlabs' | 'fish_speech'
        self.api_key = api_key        # str | None
        self.voice_id = voice_id      # str | None
        self.model = model            # str | None
        self.speed = speed            # float | None
        self.fish_url = fish_url      # str — Fish Speech base URL
        self.pitch = pitch            # str | None — Silero SSML pitch


async def _load_tts_config(tenant_id: uuid.UUID, db: AsyncSession) -> _TTSConfig:
    """Resolve TTS provider for a tenant.

    Priority:
      1. Tenant has tts_provider='elevenlabs' with tts_api_key_enc → use it.
      2. Tenant has tts_provider='fish_speech' → use local Fish Speech (optionally tts_fish_url).
      3. Tenant has tts_provider='system' (or NULL/empty) → fall back to .env:
         • If ELEVENLABS_API_KEY is set → ElevenLabs with global key.
         • Otherwise → Fish Speech with system TTS_URL.
    """
    try:
        result = await db.execute(
            select(
                TenantShellConfig.tts_provider,
                TenantShellConfig.tts_api_key_enc,
                TenantShellConfig.tts_voice_id,
                TenantShellConfig.tts_model,
                TenantShellConfig.tts_speed,
                TenantShellConfig.tts_pitch,
                TenantShellConfig.tts_fish_url,
            ).where(TenantShellConfig.tenant_id == tenant_id)
        )
        row = result.first()
    except Exception as e:
        logger.warning("Failed to load TTS config for tenant %s: %s", tenant_id, e)
        row = None

    provider_cfg = (row.tts_provider or "system") if row else "system"

    if provider_cfg == "elevenlabs":
        # Tenant's own ElevenLabs key
        api_key: str | None = None
        if row and getattr(row, "tts_api_key_enc", None):
            try:
                api_key = decrypt_value(row.tts_api_key_enc)
            except Exception:
                pass
        if api_key:
            voice_id = (row.tts_voice_id if row else None) or settings.ELEVENLABS_VOICE_ID
            model = (row.tts_model if row else None) or settings.ELEVENLABS_MODEL
            speed = (row.tts_speed if row else None)
            return _TTSConfig("elevenlabs", api_key, voice_id, model, speed, "")
        # Key missing — fall through to system

    elif provider_cfg == "fish_speech":
        fish_url = ((row.tts_fish_url if row else None) or "").rstrip("/") or settings.TTS_URL.rstrip("/")
        speed = row.tts_speed if row else None
        return _TTSConfig("fish_speech", None, None, None, speed, fish_url)

    elif provider_cfg == "silero":
        # Local Silero TTS v4: very fast (~0.1-1.5s), WAV output, GPU-accelerated.
        # tts_fish_url reused as local TTS URL override (applies to any local provider).
        # tts_voice_id = speaker name (e.g. 'xenia', 'mykyta').
        silero_url = ((row.tts_fish_url if row else None) or "").rstrip("/") or settings.SILERO_TTS_URL.rstrip("/")
        voice_id = (row.tts_voice_id if row else None) or None  # None = auto from lang
        speed = row.tts_speed if row else None
        pitch = row.tts_pitch if row else None
        return _TTSConfig("silero", None, voice_id, None, speed, silero_url, pitch)

    # 'system' or fallback
    if settings.ELEVENLABS_API_KEY:
        voice_id = settings.ELEVENLABS_VOICE_ID
        model = settings.ELEVENLABS_MODEL
        return _TTSConfig("elevenlabs", settings.ELEVENLABS_API_KEY, voice_id, model, None, "")
    else:
        # Default local: Silero (fast) → Fish Speech fallback
        silero_url = settings.SILERO_TTS_URL.rstrip("/")
        return _TTSConfig("silero", None, None, None, None, silero_url)


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


# ── Number-to-words normalization for Silero TTS ────────────────────────────
# Silero v4 doesn't expand numbers itself — digits get skipped or garbled.
# We convert before sending: IPs, addresses, integers, simple decimals.
try:
    from num2words import num2words as _n2w
    _N2W_OK = True
except ImportError:
    _N2W_OK = False

import re as _re_num

# 4-octet IP address
_IP_RE     = _re_num.compile(r'\b(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\b')
# MAC address (protect from integer conversion)
_MAC_RE    = _re_num.compile(r'\b(?:[0-9A-Fa-f]{2}[:\-]){5}[0-9A-Fa-f]{2}\b')
# House-number fraction  26/1, 15/2
_FRAC_RE   = _re_num.compile(r'\b(\d{1,4})/(\d{1,3})\b')
# Decimal like 1.5, 3.14 — NOT preceded by letter/digit (skips v1.5, Qwen3)
_DECIMAL_RE = _re_num.compile(r'(?<![a-zA-Zа-яА-ЯёЁіїєґ\d])(\d+)[.,](\d{1,4})(?![.\d])')
# Standalone integer: word-boundary, not part of alphanumeric token
_INT_RE    = _re_num.compile(r'\b(\d+)\b')
# Placeholder tag to protect already-converted spans
_PLACEHOLDER = "\x00"
# Emoji / pictograph Unicode ranges (strip before TTS)
_EMOJI_RE = _re_num.compile(
    "["
    "\U0001F300-\U0001FAFF"   # misc symbols, emoticons, transport
    "\U00002600-\U000027BF"   # misc symbols, dingbats
    "\U0001F1E0-\U0001F1FF"   # flag sequences
    "\U0000FE00-\U0000FE0F"   # variation selectors
    "\U0001F900-\U0001F9FF"   # supplemental symbols
    "]+",
    _re_num.UNICODE
)
# Hostname/domain dot: letter-or-digit DOT letter (covers google.com, mail.ru)
# Runs once per dot so mail.google.com → mail точка google точка com
_DOMAIN_DOT_RE = _re_num.compile(r'(?<=[a-zA-Z\d])\.(?=[a-zA-Z])')

# Address / administrative abbreviations → spoken form (compiled once at import).
# Order matters when patterns overlap: put longer abbreviations first.
# \b works with Cyrillic in Python 3 Unicode mode (Cyrillic is \w).
_ADDR_ABBR_RU: list[tuple] = [
    (_re_num.compile(r'\bул\.\s*',         _re_num.IGNORECASE), 'улица '),
    (_re_num.compile(r'\bпросп\.\s*',      _re_num.IGNORECASE), 'проспект '),
    (_re_num.compile(r'\bпр\.\s*',         _re_num.IGNORECASE), 'проспект '),
    (_re_num.compile(r'\bпер\.\s*',        _re_num.IGNORECASE), 'переулок '),
    (_re_num.compile(r'\bпл\.\s*',         _re_num.IGNORECASE), 'площадь '),
    (_re_num.compile(r'\bб-р\b',           _re_num.IGNORECASE), 'бульвар'),
    (_re_num.compile(r'\bнаб\.\s*',        _re_num.IGNORECASE), 'набережная '),
    (_re_num.compile(r'\bш\.\s*(?=[А-ЯЁ])',_re_num.IGNORECASE), 'шоссе '),
    (_re_num.compile(r'\bпос\.\s*',        _re_num.IGNORECASE), 'посёлок '),
    (_re_num.compile(r'\bпгт\.?\s*',       _re_num.IGNORECASE), 'посёлок городского типа '),
    (_re_num.compile(r'\bкорп\.\s*',       _re_num.IGNORECASE), 'корпус '),
    (_re_num.compile(r'\bкв\.\s*(?=\d)',   _re_num.IGNORECASE), 'квартира '),
    (_re_num.compile(r'\bд\.\s*(?=\d)',    _re_num.IGNORECASE), 'дом '),
    (_re_num.compile(r'\bр-н\b',           _re_num.IGNORECASE), 'район'),
    (_re_num.compile(r'\bобл\.\s*',        _re_num.IGNORECASE), 'область '),
    (_re_num.compile(r'\bг\.\s*(?=[А-ЯЁ])',_re_num.IGNORECASE), 'город '),
    (_re_num.compile(r'\bс\.\s*(?=[А-ЯЁ])',_re_num.IGNORECASE), 'село '),
]
_ADDR_ABBR_UA: list[tuple] = [
    (_re_num.compile(r'\bвул\.\s*',              _re_num.IGNORECASE), 'вулиця '),
    (_re_num.compile(r'\bпросп\.\s*',            _re_num.IGNORECASE), 'проспект '),
    (_re_num.compile(r'\bпр\.\s*',               _re_num.IGNORECASE), 'проспект '),
    (_re_num.compile(r'\bпров\.\s*',             _re_num.IGNORECASE), 'провулок '),
    (_re_num.compile(r'\bпл\.\s*',               _re_num.IGNORECASE), 'площа '),
    (_re_num.compile(r'\bбуд\.\s*(?=\d)',        _re_num.IGNORECASE), 'будинок '),
    (_re_num.compile(r'\bкв\.\s*(?=\d)',         _re_num.IGNORECASE), 'квартира '),
    (_re_num.compile(r'\bкорп\.\s*',             _re_num.IGNORECASE), 'корпус '),
    (_re_num.compile(r'\bр-н\b',                 _re_num.IGNORECASE), 'район'),
    (_re_num.compile(r'\bобл\.\s*',              _re_num.IGNORECASE), 'область '),
    (_re_num.compile(r'\bм\.\s*(?=[А-ЯІЇЄҐ])',  _re_num.IGNORECASE), 'місто '),
]


# ── English tech units → Cyrillic words (step 0.6, before number conversion) ─
# Longest patterns first so "Mbps" is matched before "bps" etc.
# Single-letter ambiguous units (V, A, W) are digit-anchored via lookbehind.
_EN_UNITS_RU: list[tuple] = [
    (_re_num.compile(r'\bGbps\b', _re_num.IGNORECASE), 'гигабит в секунду'),
    (_re_num.compile(r'\bMbps\b', _re_num.IGNORECASE), 'мегабит в секунду'),
    (_re_num.compile(r'\b[Kk]bps\b'),                  'килобит в секунду'),
    (_re_num.compile(r'\bbps\b',   _re_num.IGNORECASE), 'бит в секунду'),
    (_re_num.compile(r'\bGHz\b'),                       'гигагерц'),
    (_re_num.compile(r'\bMHz\b'),                       'мегагерц'),
    (_re_num.compile(r'\bkHz\b'),                       'килогерц'),
    (_re_num.compile(r'\bHz\b'),                        'герц'),
    (_re_num.compile(r'\bTB\b'),                        'терабайт'),
    (_re_num.compile(r'\bGB\b'),                        'гигабайт'),
    (_re_num.compile(r'\bMB\b'),                        'мегабайт'),
    (_re_num.compile(r'\b[Kk]B\b'),                     'килобайт'),
    (_re_num.compile(r'\bdBm\b'),                       'дБм'),
    (_re_num.compile(r'\bdB\b'),                        'децибел'),
    (_re_num.compile(r'\bms\b'),                        'мс'),
    (_re_num.compile(r'(?<=[\d\s])[µu]s\b'),            'мкс'),
    (_re_num.compile(r'(?<=\d)\s*V\b'),                 'вольт'),
    (_re_num.compile(r'(?<=\d)\s*W\b'),                 'ватт'),
    (_re_num.compile(r'\bvs\.?\b', _re_num.IGNORECASE), 'против'),
]
_EN_UNITS_UA: list[tuple] = [
    (_re_num.compile(r'\bGbps\b', _re_num.IGNORECASE), 'гігабіт за секунду'),
    (_re_num.compile(r'\bMbps\b', _re_num.IGNORECASE), 'мегабіт за секунду'),
    (_re_num.compile(r'\b[Kk]bps\b'),                  'кілобіт за секунду'),
    (_re_num.compile(r'\bbps\b',   _re_num.IGNORECASE), 'біт за секунду'),
    (_re_num.compile(r'\bGHz\b'),                       'гігагерц'),
    (_re_num.compile(r'\bMHz\b'),                       'мегагерц'),
    (_re_num.compile(r'\bkHz\b'),                       'кілогерц'),
    (_re_num.compile(r'\bHz\b'),                        'герц'),
    (_re_num.compile(r'\bTB\b'),                        'терабайт'),
    (_re_num.compile(r'\bGB\b'),                        'гігабайт'),
    (_re_num.compile(r'\bMB\b'),                        'мегабайт'),
    (_re_num.compile(r'\b[Kk]B\b'),                     'кілобайт'),
    (_re_num.compile(r'\bdBm\b'),                       'дБм'),
    (_re_num.compile(r'\bdB\b'),                        'децибел'),
    (_re_num.compile(r'\bms\b'),                        'мс'),
    (_re_num.compile(r'(?<=[\d\s])[µu]s\b'),            'мкс'),
    (_re_num.compile(r'(?<=\d)\s*V\b'),                 'вольт'),
    (_re_num.compile(r'(?<=\d)\s*W\b'),                 'ват'),
    (_re_num.compile(r'\bvs\.?\b', _re_num.IGNORECASE), 'проти'),
]

# Slash between two numeric values (ping/traceroute output):
# "21.508/21.609/21.710/0.101" → "21.508, 21.609, 21.710, 0.101"
_SLASH_NUM_RE = _re_num.compile(r'(?<=[\d.])\/(?=[\d.])')

# ALL-CAPS Latin abbreviation (2-8 letters): DNS, VLAN, SFP, BGP, ONU, OLT → spelt
_ALLCAPS_WORD_RE = _re_num.compile(r'\b([A-Z]{2,8})\b')

# Remaining Latin characters after all conversions (brand names, domain labels)
# Negative lookbehind \x01 protects MAC placeholders (\x01M…\x01).
_LATIN_REMAIN_RE = _re_num.compile(r'(?<!\x01)[a-zA-Z]+')

# English letter names for spelling out abbreviations (as used in RU/UA IT speech).
_EN_LETTER_NAMES_RU: dict[str, str] = {
    'A': 'эй', 'B': 'би',  'C': 'си',   'D': 'ди',    'E': 'и',
    'F': 'эф', 'G': 'джи', 'H': 'эйч',  'I': 'ай',    'J': 'джей',
    'K': 'кей','L': 'эл',  'M': 'эм',   'N': 'эн',    'O': 'оу',
    'P': 'пи', 'Q': 'кью', 'R': 'ар',   'S': 'эс',    'T': 'ти',
    'U': 'ю',  'V': 'ви',  'W': 'даблью','X': 'экс',  'Y': 'уай', 'Z': 'зэд',
}
_EN_LETTER_NAMES_UA: dict[str, str] = {
    'A': 'ей', 'B': 'бі',  'C': 'сі',   'D': 'ді',    'E': 'і',
    'F': 'еф', 'G': 'джі', 'H': 'ейч',  'I': 'ай',    'J': 'джей',
    'K': 'кей','L': 'ел',  'M': 'ем',   'N': 'ен',    'O': 'оу',
    'P': 'пі', 'Q': 'кью', 'R': 'ар',   'S': 'ес',    'T': 'ті',
    'U': 'ю',  'V': 'ві',  'W': 'дабл-ю','X': 'екс', 'Y': 'уай', 'Z': 'зед',
}

# Simple Latin→Cyrillic phonetic map for lowercase words (brand names, domain labels).
_LATIN_TO_CYR_RU: dict[str, str] = {
    'a': 'а', 'b': 'б', 'c': 'к', 'd': 'д', 'e': 'е', 'f': 'ф',
    'g': 'г', 'h': 'х', 'i': 'и', 'j': 'дж','k': 'к', 'l': 'л',
    'm': 'м', 'n': 'н', 'o': 'о', 'p': 'п', 'q': 'кв','r': 'р',
    's': 'с', 't': 'т', 'u': 'у', 'v': 'в', 'w': 'в', 'x': 'кс',
    'y': 'й', 'z': 'з',
}
_LATIN_TO_CYR_UA: dict[str, str] = {**_LATIN_TO_CYR_RU, 'i': 'і', 'y': 'й'}


def _num_words(n: int, lang: str) -> str:
    try:
        return _n2w(n, lang='uk' if lang == 'ua' else 'ru')
    except Exception:
        return str(n)


def _normalize_numbers_for_silero(text: str, lang: str = 'ru') -> str:
    """Convert numeric expressions, abbreviations and math symbols to spoken word form for Silero TTS.

    Order matters:
      0. Strip emoji.
      0.5 Expand address abbreviations (ул.→улица, пр.→проспект, пер.→переулок, …).
      1. Protect MAC addresses (leave unchanged).
      1.5 Math/special symbols → words (%, =, +, -, ×, ÷, №, ≥, ≤, ≠).
      2. Convert IP addresses (each octet → word, separated by "точка"/"крапка").
      3. Convert house fractions 26/1 → "двадцать шесть дробь один".
      4. Convert simple decimals 1.5 → "один запятая пять".
      5. Convert remaining standalone integers.
    """
    if not _N2W_OK:
        return text

    # 0. Strip emoji (Silero can't pronounce them; just drop)
    text = _EMOJI_RE.sub('', text)

    # 0.5. Expand address/administrative abbreviations (ул. → улица, пр. → проспект …)
    # Must run before IP and domain-dot processing, which also inspect dots.
    _abbr_table = _ADDR_ABBR_UA if lang == 'ua' else _ADDR_ABBR_RU
    for _abbr_re, _abbr_word in _abbr_table:
        text = _abbr_re.sub(_abbr_word, text)

    # 0.6. Expand English tech units before number conversion so digit-anchored
    # lookbehinds still see the raw digits (ms, Mbps, GHz, GB, dBm …)
    _unit_table = _EN_UNITS_UA if lang == 'ua' else _EN_UNITS_RU
    for _u_re, _u_word in _unit_table:
        text = _u_re.sub(_u_word, text)

    # 0.7. Slash between numeric values → comma separator
    # Handles ping output: "21.508/21.609/21.710/0.101" → "21.508, 21.609, 21.710, 0.101"
    text = _SLASH_NUM_RE.sub(', ', text)

    point    = 'крапка'             if lang == 'ua' else 'точка'
    frac     = 'дріб'               if lang == 'ua' else 'дробь'
    comma    = 'кома'               if lang == 'ua' else 'запятая'
    w_pct    = 'відсоток'           if lang == 'ua' else 'процент'
    w_eq     = 'дорівнює'           if lang == 'ua' else 'равно'
    w_neq    = 'не дорівнює'        if lang == 'ua' else 'не равно'
    w_gte    = 'більше або дорівнює' if lang == 'ua' else 'больше или равно'
    w_lte    = 'менше або дорівнює'  if lang == 'ua' else 'меньше или равно'
    w_times  = 'помножити на'        if lang == 'ua' else 'умножить на'
    w_div    = 'поділити на'         if lang == 'ua' else 'разделить на'
    w_plus   = 'плюс'
    w_minus  = 'мінус'              if lang == 'ua' else 'минус'
    w_num    = 'номер'

    # 1. Protect MACs by replacing with placeholder (hex index prefixed with "M"
    #    so _INT_RE's word-boundary digit check won't touch the index digits).
    macs: list[str] = []
    def save_mac(m):
        idx = len(macs)
        macs.append(m.group(0))
        return f"\x01M{idx:04X}\x01"   # e.g. \x01M0000\x01 — no bare digit boundary
    text = _MAC_RE.sub(save_mac, text)

    # 1.5 Math / special symbols → words
    # № (before number or standalone)
    text = _re_num.sub(r'№\s*', f'{w_num} ', text)
    # Multi-char comparison operators first (before single-char = < >)
    text = _re_num.sub(r'\s*≠\s*', f' {w_neq} ', text)
    text = _re_num.sub(r'\s*≥\s*', f' {w_gte} ', text)
    text = _re_num.sub(r'\s*≤\s*', f' {w_lte} ', text)
    # = (plain equals, common in "IP=192...", "a=5")
    text = _re_num.sub(r'\s*=\s*', f' {w_eq} ', text)
    # × ✕ (multiplication cross)
    text = _re_num.sub(r'\s*[×✕]\s*', f' {w_times} ', text)
    # ÷ (division)
    text = _re_num.sub(r'\s*÷\s*', f' {w_div} ', text)
    # % (percent after digit or standalone)
    text = _re_num.sub(r'%', f' {w_pct}', text)
    # Unicode minus sign U+2212 (−) → minus word
    text = _re_num.sub(r'−\s*', f'{w_minus} ', text)
    # ASCII minus before digit when NOT preceded by a letter/digit
    # (unary negative: "-5°C", "−20", but NOT "из-за", "wi-fi")
    text = _re_num.sub(r'(?<![а-яА-ЯёЁіїєґa-zA-Z\d])-(?=\d)', f'{w_minus} ', text)
    # + sign: math context — preceded by digit/space OR followed by digit/space
    # Covers "+30 грн", "2+2", "30+", "+7 …"
    # But NOT mid-word (very unlikely in ru/uk but guard anyway)
    text = _re_num.sub(r'(?<=\d)\s*\+\s*(?=\d)', f' {w_plus} ', text)   # 2+2
    text = _re_num.sub(r'(?<=\d)\s*\+(?=\s|$)',   f' {w_plus}', text)    # 30+
    text = _re_num.sub(r'(?<=\s)\+\s*(?=\d)',      f' {w_plus} ', text)   # " +30"
    text = _re_num.sub(r'(?<=\s)\+\s*(?=\s)',      f' {w_plus} ', text)   # " + "
    # digit - digit (math subtraction, e.g. "10-3" or "10 - 3")
    text = _re_num.sub(r'(?<=\d)\s*-\s*(?=\d)', f' {w_minus} ', text)   # 10-3

    # 2. IP addresses
    def ip_repl(m):
        parts = [_num_words(int(x), lang) for x in m.groups()]
        return f' {point} '.join(parts)
    text = _IP_RE.sub(ip_repl, text)

    # 2.5 Domain / hostname dots: google.com → google точка com
    text = _DOMAIN_DOT_RE.sub(f' {point} ', text)

    # 2.7 Full dates DD.MM.YYYY → "одиннадцатого июня две тысячи двадцать
    # шестого года". MUST run before the decimal rule, which otherwise eats
    # "06.2026" as a decimal number and mangles the date.
    _months_ru = [None, 'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
                  'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря']
    _months_ua = [None, 'січня', 'лютого', 'березня', 'квітня', 'травня', 'червня',
                  'липня', 'серпня', 'вересня', 'жовтня', 'листопада', 'грудня']
    _months = _months_ua if lang == 'ua' else _months_ru
    _w_year = 'року' if lang == 'ua' else 'года'

    def _ord_gen(n: int) -> str:
        """Ordinal in genitive: 11 → одиннадцатого / одинадцятого."""
        try:
            w = _n2w(n, lang='uk' if lang == 'ua' else 'ru', to='ordinal')
        except Exception:
            return _num_words(n, lang)
        for suf, rep in (('ый', 'ого'), ('ой', 'ого'), ('ій', 'ього'), ('ий', 'ого')):
            if w.endswith(suf):
                return w[: -len(suf)] + rep
        return w

    def date_repl(m):
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if not (1 <= d <= 31 and 1 <= mo <= 12 and 1900 <= y <= 2199):
            return m.group(0)
        return f"{_ord_gen(d)} {_months[mo]} {_ord_gen(y)} {_w_year}"
    text = _re_num.sub(r'\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b', date_repl, text)

    # 3. House/address fractions  26/1 → двадцать шесть дробь один
    def frac_repl(m):
        return f'{_num_words(int(m.group(1)), lang)} {frac} {_num_words(int(m.group(2)), lang)}'
    text = _FRAC_RE.sub(frac_repl, text)

    # 4. Simple decimals (not version-like)
    def decimal_repl(m):
        int_part = _num_words(int(m.group(1)), lang)
        frac_str = m.group(2).rstrip('0') or '0'
        # Spell fractional digits individually for clarity ("три запятая один четыре")
        frac_parts = ' '.join(_num_words(int(d), lang) for d in frac_str)
        return f'{int_part} {comma} {frac_parts}'
    text = _DECIMAL_RE.sub(decimal_repl, text)

    # 5. Remaining standalone integers
    def int_repl(m):
        return _num_words(int(m.group(1)), lang)
    text = _INT_RE.sub(int_repl, text)

    # 6. Latin letters remaining after all numeric/unit conversions.
    #    6a. ALL-CAPS abbreviations (2-8 letters) → spelled letter-by-letter
    #        DNS→"ди эн эс", VLAN→"ви эл эй эн", SFP→"эс эф пи" (ru)
    _lnames = _EN_LETTER_NAMES_UA if lang == 'ua' else _EN_LETTER_NAMES_RU
    _l2cyr  = _LATIN_TO_CYR_UA    if lang == 'ua' else _LATIN_TO_CYR_RU

    def _spell_allcaps(m: _re_num.Match) -> str:
        return ' '.join(_lnames.get(c, c) for c in m.group(1))

    text = _ALLCAPS_WORD_RE.sub(_spell_allcaps, text)

    #    6b. Remaining lowercase/mixed-case Latin words → phonetic transliteration
    #        google→гугл/гоогле, com→ком, MikroTik→мікротік
    #        Note: \x01 lookbehind prevents touching MAC placeholder markers.
    def _transliterate(m: _re_num.Match) -> str:
        return ''.join(_l2cyr.get(c.lower(), c) for c in m.group(0))

    text = _LATIN_REMAIN_RE.sub(_transliterate, text)

    # Restore MACs
    def restore_mac(m):
        return macs[int(m.group(1), 16)]
    text = _re_num.sub(r'\x01M([0-9A-F]+)\x01', restore_mac, text)

    return text


def _is_hallucination(text: str) -> bool:
    """Cheap check: Whisper boilerplate fired on silence/noise. The model
    rarely produces these for real speech, so dropping them is safe."""
    if not text or len(text.strip()) < 2:
        return False
    return any(p.search(text) for p in _HALLUCINATION_PATTERNS)


@router.post("/stt", response_model=STTResponse, dependencies=[Depends(voice_limiter)])
async def speech_to_text(
    tenant_id: uuid.UUID,
    file: UploadFile = File(...),
    language: str | None = Form(default=None),
    auth: TenantAuthContext = Depends(get_current_tenant_auth_context),
    db: AsyncSession = Depends(get_db),
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

    stt_cfg = await _load_stt_config(tenant_id, db)

    # Load domain vocabulary for post-processing normalization (cached, non-blocking)
    vocab = await get_tenant_vocab(
        tenant_id,
        stt_cfg.vocab_source,
        stt_cfg.vocab_dsn_enc,
    )

    try:
        async with httpx.AsyncClient(timeout=settings.STT_TIMEOUT_SECONDS) as client:
            data = {"model": (None, settings.STT_MODEL), "response_format": (None, "json")}
            if effective_lang:
                data["language"] = (None, effective_lang)
            if stt_cfg.initial_prompt:
                data["prompt"] = (None, stt_cfg.initial_prompt)
            if stt_cfg.hotwords:
                data["hotwords"] = (None, stt_cfg.hotwords)
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
            # Fix Whisper's address-fraction normalisation: "26.1" → "26/1"
            if text:
                text = fix_address_fractions(text)
            # Post-processing: fuzzy-correct domain terms (streets, ISP jargon, …)
            if vocab and text:
                blacklist = frozenset(
                    w.lower() for w in (stt_cfg.vocab_source or {}).get("blacklist", [])
                )
                text = normalize_transcript(text, vocab, stt_cfg.fuzzy_threshold, blacklist)
            return STTResponse(text=text)
    except httpx.HTTPStatusError as e:
        logger.error("STT HTTP %s: %s", e.response.status_code, (e.response.text or "")[:300])
        raise HTTPException(status_code=502, detail=f"STT upstream error {e.response.status_code}")
    except Exception as e:
        logger.exception("STT failed")
        raise HTTPException(status_code=502, detail=f"STT failed: {str(e)[:200]}")


import re as _re_tts

# Match any run of consecutive lines that start with "|" — handles tables with
# and without trailing pipe (both are common in LLM output).
_TABLE_BLOCK_RE = _re_tts.compile(r'(?:^\|[^\n]*\n?)+', _re_tts.MULTILINE)

# Ukrainian-specific letters absent in Russian; Russian-specific letters absent in Ukrainian.
_UK_CHARS = frozenset('іїєґІЇЄҐ')
_RU_CHARS = frozenset('ыёъэЫЁЪЭ')

_TTS_TABLE_PH: dict[str, str] = {
    'ru': '\nДанные представлены в таблице.\n',
    'uk': '\nДані представлено у таблиці.\n',
    'en': '\nData is shown in a table.\n',
}
_TTS_CODE_PH: dict[str, str] = {
    'ru': '\nСмотрите код в ответе.\n',
    'uk': '\nДивіться код у відповіді.\n',
    'en': '\nSee the code in the response.\n',
}


def _detect_lang(text: str) -> str:
    """Quick dominant-language detection: 'ru', 'uk', or 'en'.

    Uses distinctive letter sets (no external libs needed):
      • Ukrainian-exclusive chars (і, ї, є, ґ) → 'uk'
      • Russian-exclusive chars (ы, ё, ъ, э)   → 'ru'
      • Neither but Cyrillic present            → 'ru' (default Cyrillic)
      • Otherwise                               → 'en'
    """
    uk = sum(1 for c in text if c in _UK_CHARS)
    ru = sum(1 for c in text if c in _RU_CHARS)
    if uk > ru:
        return 'uk'
    cyrillic = sum(1 for c in text if 'Ѐ' <= c <= 'ӿ')
    if cyrillic > len(text) * 0.25:
        return 'ru'
    return 'en'


def _sanitize_for_tts(text: str) -> str:
    """Prepare LLM output text for TTS synthesis.

    - Detect dominant language of the response and use matching placeholders.
    - Replace markdown tables with a spoken placeholder.
    - Remove code fences (``` blocks) — replace with a brief note.
    - Strip inline markdown syntax (**, __, *, _, `).
    - Collapse excessive blank lines.
    """
    lang = _detect_lang(text)
    table_ph = _TTS_TABLE_PH[lang]
    code_ph  = _TTS_CODE_PH[lang]

    # Replace markdown table blocks (consecutive | lines) with placeholder.
    text = _TABLE_BLOCK_RE.sub(lambda _m: table_ph, text)

    # Replace fenced code blocks.
    text = _re_tts.sub(r'```[^\n]*\n[\s\S]*?```', code_ph, text)

    # Strip inline markdown: bold, italic, inline code.
    text = _re_tts.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = _re_tts.sub(r'__(.+?)__', r'\1', text)
    text = _re_tts.sub(r'\*(.+?)\*', r'\1', text)
    text = _re_tts.sub(r'_(.+?)_', r'\1', text)
    text = _re_tts.sub(r'`([^`]+)`', r'\1', text)
    # Collapse 3+ blank lines to 2.
    text = _re_tts.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


class TTSRequest(BaseModel):
    text: str
    voice: str | None = None
    # mp3 / wav / flac / opus / aac — passed to upstream as `response_format`.
    format: str = "mp3"
    # Playback speed multiplier. None → backend default (settings.TTS_SPEED).
    # OpenAI-spec range is 0.25..4.0; we clamp.
    speed: float | None = None


# ── TTS sentence chunking ───────────────────────────────────────────────────
# XTTS / openedai-speech buffers internally and only returns audio after the
# ENTIRE input is synthesised. For a long LLM response that means 10+ s of
# silence. We split into sentence-sized chunks and stream each one in order;
# the browser can start playing the first chunk (~500 ms) while the rest are
# being synthesised.

# Sentence boundary: end-of-sentence punctuation followed by whitespace or EOL.
# Covers English, Russian/Ukrainian. We intentionally include "…" and "—" splits.
_SENT_SPLIT_RE = _re_tts.compile(
    r'(?<=[.!?…])\s+'            # after . ! ? … + whitespace
    r'|(?<=\n)\s*(?=\S)'         # newline (paragraph break)
    r'|(?<=[.!?…])"?\s*$',       # end of string
    _re_tts.MULTILINE,
)

# Minimum chunk length (chars) to send as a single TTS request.
# Very short pieces cause disproportionate per-request overhead on XTTS.
_TTS_MIN_CHUNK = 30


def _split_tts_chunks(text: str, max_len: int = 300) -> list[str]:
    """Split *text* into TTS-friendly sentence chunks.

    Strategy:
      1. Split on sentence boundaries.
      2. Merge consecutive pieces that are below _TTS_MIN_CHUNK.
      3. Never exceed max_len (hard-split on whitespace if needed).
    """
    raw = [s.strip() for s in _SENT_SPLIT_RE.split(text) if s and s.strip()]
    if not raw:
        return [text] if text.strip() else []

    chunks: list[str] = []
    current = ""
    for piece in raw:
        # Hard-split oversized pieces at whitespace boundaries
        while len(piece) > max_len:
            split_at = piece.rfind(' ', 0, max_len)
            if split_at == -1:
                split_at = max_len
            head, piece = piece[:split_at].strip(), piece[split_at:].strip()
            if current:
                chunks.append((current + ' ' + head).strip())
                current = ""
            else:
                chunks.append(head)

        if not piece:
            continue
        candidate = (current + ' ' + piece).strip() if current else piece
        if len(candidate) > max_len:
            if current:
                chunks.append(current)
            current = piece
        else:
            current = candidate

    if current:
        chunks.append(current)

    # Merge trailing stub into previous chunk
    if len(chunks) >= 2 and len(chunks[-1]) < _TTS_MIN_CHUNK:
        chunks[-2] = (chunks[-2] + ' ' + chunks[-1]).strip()
        chunks.pop()

    return [c for c in chunks if c]


@router.post("/tts", dependencies=[Depends(voice_limiter)])
async def text_to_speech(
    tenant_id: uuid.UUID,
    body: TTSRequest,
    auth: TenantAuthContext = Depends(get_current_tenant_auth_context),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """Synthesize text into an audio stream.

    Provider priority (per-tenant):
      1. Tenant configured tts_provider='elevenlabs' with their own API key.
      2. Tenant configured tts_provider='fish_speech' (optional custom URL).
      3. tts_provider='system' or NULL → fall back to global .env:
         • ELEVENLABS_API_KEY set → ElevenLabs (cloud, high quality, ~300 ms).
         • Otherwise → local Fish Speech (sentence-chunked streaming).
    """
    _verify_tenant(tenant_id, auth.tenant)
    text = _sanitize_for_tts((body.text or "").strip())
    if not text:
        raise HTTPException(status_code=400, detail="Empty text")
    if len(text) > 4000:
        text = text[:4000]

    fmt = (body.format or "mp3").lower()
    if fmt not in ("mp3", "wav", "flac", "opus", "aac"):
        fmt = "mp3"
    mime = {"mp3": "audio/mpeg", "wav": "audio/wav", "flac": "audio/flac",
            "opus": "audio/ogg", "aac": "audio/aac"}.get(fmt, "audio/mpeg")

    tts_cfg = await _load_tts_config(tenant_id, db)

    # ── ElevenLabs path ────────────────────────────────────────────────────────
    if tts_cfg.provider == "elevenlabs":
        voice_id = tts_cfg.voice_id or settings.ELEVENLABS_VOICE_ID
        model = tts_cfg.model or settings.ELEVENLABS_MODEL
        el_url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"
        el_payload = {
            "text": text,
            "model_id": model,
            "voice_settings": {
                "stability": 0.45,
                "similarity_boost": 0.80,
                "style": 0.0,
                "use_speaker_boost": True,
            },
        }
        el_headers = {
            "xi-api-key": tts_cfg.api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        }
        # Turbo v2.5 only supports mp3; force mp3 for ElevenLabs
        fmt = "mp3"
        mime = "audio/mpeg"

        logger.debug("TTS: ElevenLabs %d chars, model=%s, voice=%s", len(text), model, voice_id)

        async def _el_gen():
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    async with client.stream("POST", el_url, json=el_payload, headers=el_headers) as resp:
                        if resp.status_code != 200:
                            body_bytes = await resp.aread()
                            logger.error("ElevenLabs HTTP %s: %s", resp.status_code, body_bytes[:300])
                            return
                        async for chunk in resp.aiter_bytes():
                            yield chunk
            except Exception as exc:
                logger.error("ElevenLabs TTS failed: %s", exc)

        return StreamingResponse(_el_gen(), media_type=mime)

    # ── Silero TTS v4 path (GPU, fast ~0.1-1.5s, WAV output) ─────────────────
    # Silero is so fast that sentence-chunking adds overhead without benefit —
    # we send the whole sanitized text in one request. Language is auto-detected
    # from the text; speaker defaults to xenia (ru) / mykyta (ua).
    if tts_cfg.provider == "silero":
        silero_base = tts_cfg.fish_url or settings.SILERO_TTS_URL.rstrip("/")
        lang = _detect_lang(text)
        silero_lang = "ua" if lang == "uk" else "ru"
        # tts_voice_id holds speaker name if configured; otherwise use system defaults
        # tts_voice_id is shared across providers — an ElevenLabs voice id may
        # linger here after a provider switch. Only accept silero-looking names.
        _vid = tts_cfg.voice_id or ""
        speaker = _vid if re.fullmatch(r"[a-z]+_[a-z0-9_]+", _vid) else (
            settings.SILERO_SPEAKER_UA if silero_lang == "ua" else settings.SILERO_SPEAKER_RU
        )
        # Silero v4 doesn't expand numbers itself — normalize before synthesis
        text_silero = _normalize_numbers_for_silero(text, silero_lang)
        out_fmt = "mp3" if (body.format or "").lower() == "mp3" else "wav"
        silero_payload = {"text": text_silero, "lang": silero_lang, "speaker": speaker, "sample_rate": 48000,
                          "speed": tts_cfg.speed or 1.0, "pitch": getattr(tts_cfg, "pitch", None),
                          "format": out_fmt}
        logger.debug("TTS: Silero %d→%d chars, lang=%s, speaker=%s, url=%s",
                     len(text), len(text_silero), silero_lang, speaker, silero_base)

        async def _silero_gen():
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.post(f"{silero_base}/tts", json=silero_payload)
                    if resp.status_code != 200:
                        logger.error("Silero HTTP %s: %s", resp.status_code, (resp.text or "")[:300])
                        return
                    yield resp.content
            except Exception as exc:
                logger.error("Silero TTS failed: %s", exc)

        return StreamingResponse(_silero_gen(), media_type="audio/mpeg" if out_fmt == "mp3" else "audio/wav")

    # ── Local Fish Speech 1.5 path (sentence-chunked, MsgPack binary API) ──────
    # Fish Speech 1.5 uses ormsgpack serialization, not JSON.
    # Streaming to the browser is achieved at the sentence level: each chunk is
    # synthesised by Fish Speech and forwarded to the client as it completes,
    # so the browser starts playing the first chunk (~3-5 s) while subsequent
    # chunks are still being synthesised.
    #
    # Format: MP3 is used (not WAV) because MP3 is frame-based — concatenated
    # MP3 files play as a seamless stream. WAV would require header-stripping
    # logic (multiple RIFF headers break playback). Fish Speech streaming=True
    # returns raw headerless PCM which browsers cannot decode directly.
    import ormsgpack as _msgpack  # type: ignore[import]

    fs_fmt = "mp3"
    mime = "audio/mpeg"
    fish_base_url = tts_cfg.fish_url or settings.TTS_URL.rstrip("/")

    chunks = _split_tts_chunks(text)
    logger.debug("TTS: FishSpeech1.5 %d chunk(s) for %d chars, url=%s", len(chunks), len(text), fish_base_url)

    async def _fs_gen():
        async with httpx.AsyncClient(timeout=settings.TTS_TIMEOUT_SECONDS) as client:
            for i, chunk_text in enumerate(chunks):
                lang = _detect_lang(chunk_text)
                ref_id = settings.FISH_SPEECH_REF_UK if lang == "uk" else settings.FISH_SPEECH_REF_RU
                fs_req = {
                    "text": chunk_text,
                    "reference_id": ref_id,
                    "format": fs_fmt,
                    "normalize": True,
                    "streaming": False,
                    "use_memory_cache": "on",
                }
                body_bytes = _msgpack.packb(fs_req)
                try:
                    async with client.stream(
                        "POST",
                        f"{fish_base_url}/v1/tts",
                        content=body_bytes,
                        headers={"Content-Type": "application/msgpack"},
                    ) as upstream:
                        upstream.raise_for_status()
                        async for data in upstream.aiter_bytes():
                            yield data
                except httpx.HTTPStatusError as exc:
                    logger.error("TTS chunk %d HTTP %s: %s", i, exc.response.status_code,
                                 (exc.response.text or "")[:300])
                    return
                except Exception as exc:
                    logger.error("TTS chunk %d failed: %s", i, exc)
                    return

    return StreamingResponse(_fs_gen(), media_type=mime)


@router.websocket("/stt-stream")
async def stt_stream_proxy(
    websocket: WebSocket,
    tenant_id: uuid.UUID,
    api_key: str | None = Query(default=None, alias="api_key"),
    authorization: str | None = Query(default=None, alias="authorization"),
):
    """WebSocket proxy: browser ↔ WhisperLive streaming STT.

    Auth: pass the tenant API key as ?api_key=<key> query param
    (browser WebSocket API can't send custom headers).

    Protocol (collabora/WhisperLive):
      • Client → Server: JSON config first (text frame), then binary Float32 chunks at 16 kHz mono.
      • Server → Client: JSON with {segments:[{text, completed},...], language}.

    The proxy intercepts the initial JSON config message and injects the tenant's
    stt_initial_prompt and stt_hotwords so the browser never needs to know about them.
    """
    import json as _json
    from app.core.security import hash_api_key
    from app.models.tenant_api_key import TenantApiKey
    import websockets as _ws

    # -- Auth ----------------------------------------------------------------
    raw_key = api_key or (authorization.removeprefix("Bearer ").strip() if authorization else None)
    if not raw_key:
        await websocket.close(code=4401, reason="API key required")
        return

    db_gen = get_db()
    db = await db_gen.__anext__()
    try:
        key_hash = hash_api_key(raw_key)
        ak = (
            await db.execute(
                select(TenantApiKey).where(
                    TenantApiKey.key_hash == key_hash,
                    TenantApiKey.tenant_id == tenant_id,
                    TenantApiKey.is_active.is_(True),
                )
            )
        ).scalars().first()
        if not ak:
            await websocket.close(code=4403, reason="Invalid API key")
            return

        # Load STT vocabulary while we still have the db session open
        initial_prompt, hotwords = await _load_stt_vocab(tenant_id, db)
    finally:
        await db_gen.aclose()

    await websocket.accept()

    wl_url = settings.WHISPER_LIVE_WS_URL
    try:
        async with _ws.connect(wl_url, ping_interval=20, ping_timeout=10) as upstream:
            async def fwd_browser_to_wl():
                """Forward frames from browser → WhisperLive.

                The first text message is the WhisperLive JSON config — we inject
                stt_initial_prompt and stt_hotwords before forwarding it.
                Subsequent binary messages are audio chunks forwarded as-is.
                """
                first_json_done = False
                try:
                    while True:
                        raw = await websocket.receive()
                        if raw.get("type") == "websocket.disconnect":
                            break
                        text = raw.get("text")
                        data = raw.get("bytes")
                        if text is not None:
                            if not first_json_done:
                                first_json_done = True
                                # Inject vocab into the initial config frame
                                try:
                                    cfg = _json.loads(text)
                                    if initial_prompt and "initial_prompt" not in cfg:
                                        cfg["initial_prompt"] = initial_prompt
                                    if hotwords and "hotwords" not in cfg:
                                        cfg["hotwords"] = hotwords
                                    text = _json.dumps(cfg)
                                except Exception:
                                    pass  # pass through verbatim if parse fails
                            await upstream.send(text)
                        elif data is not None:
                            await upstream.send(data)
                except WebSocketDisconnect:
                    pass
                except Exception:
                    pass
                finally:
                    try:
                        await upstream.close()
                    except Exception:
                        pass

            async def fwd_wl_to_browser():
                """Forward messages from WhisperLive → browser."""
                try:
                    async for msg in upstream:
                        if isinstance(msg, str):
                            await websocket.send_text(msg)
                        else:
                            await websocket.send_bytes(msg)
                except Exception:
                    pass

            await asyncio.gather(fwd_browser_to_wl(), fwd_wl_to_browser())
    except Exception as e:
        logger.warning("stt_stream_proxy error for tenant %s: %s", tenant_id, e)
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
