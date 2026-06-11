import logging
import io
import torch
import soundfile as sf
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("silero-v5")

DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
log.info("loading v5_cis_base on %s...", DEVICE)
model = torch.package.PackageImporter("/model/v5_cis_base.pt").load_pickle("tts_models", "model")
model.to(DEVICE)
SPEAKERS = sorted(model.speakers)
log.info("model ready, %d speakers", len(SPEAKERS))

DEFAULT_SPEAKER = {"ru": "ru_saida", "ua": "ukr_roman", "uk": "ukr_roman"}

# ── Accentuators ─────────────────────────────────────────────────────────────
# cis_base expects `+` before the stressed vowel (зам+ок); its own dictionary
# is weak, so we pre-mark stress before synthesis.
log.info("loading ru accentuator (ruaccent)...")
RU_ACC = None
try:
    from ruaccent import RUAccent
    RU_ACC = RUAccent()
    RU_ACC.load(omograph_model_size="turbo", use_dictionary=True, device="CPU")
    log.info("ruaccent ready")
except Exception as e:
    log.warning("ruaccent unavailable: %s", e)

log.info("loading uk accentuator (ukrainian-word-stress)...")
UK_ACC = None
try:
    from ukrainian_word_stress import Stressifier
    UK_ACC = Stressifier()
    log.info("uk stressifier ready")
except Exception as e:
    log.warning("uk stressifier unavailable: %s", e)

_UK_VOWELS = "аеєиіїоуюяАЕЄИІЇОУЮЯ"
# ukrainian-word-stress may emit U+0301 (combining acute) or U+00B4 (acute)
_UK_STRESS_MARKS = {"\u0301", "\u00b4"}


def _uk_to_plus(text: str) -> str:
    """ukrainian-word-stress marks stress AFTER the vowel;
    Silero wants `+` BEFORE the vowel."""
    out = []
    for ch in text:
        if ch in _UK_STRESS_MARKS and out and out[-1] in _UK_VOWELS:
            v = out.pop()
            out.append("+")
            out.append(v)
        elif ch not in _UK_STRESS_MARKS:
            out.append(ch)
    return "".join(out)


def accentuate(text: str, lang: str) -> str:
    if "+" in text:
        return text  # caller already marked stress — trust them
    try:
        if lang == "ru" and RU_ACC is not None:
            return RU_ACC.process_all(text)
        if lang in ("ua", "uk") and UK_ACC is not None:
            return _uk_to_plus(UK_ACC(text))
    except Exception as e:
        log.warning("accentuation failed (%s): %s — using raw text", lang, e)
    return text


app = FastAPI(title="Silero v5 cis_base TTS (MIT)")


class TTSReq(BaseModel):
    text: str
    lang: str = "ru"           # ru | ua (compat with v4 wrapper)
    speaker: str | None = None
    sample_rate: int = 48000   # 48k: noticeably better than 24k
    format: str = "wav"        # wav | asterisk (8kHz mono 16-bit, normalized)
    accent: bool = True        # run the accentuator before synthesis


@app.get("/health")
def health():
    return {"status": "ok", "device": str(DEVICE), "speakers": len(SPEAKERS),
            "accent_ru": RU_ACC is not None, "accent_uk": UK_ACC is not None}


@app.get("/speakers")
def speakers():
    return {"speakers": SPEAKERS}


@app.post("/tts")
def tts(r: TTSReq):
    if not r.text.strip():
        raise HTTPException(400, "empty text")
    sp = r.speaker or DEFAULT_SPEAKER.get(r.lang.lower(), "ru_saida")
    if sp not in SPEAKERS:
        raise HTTPException(400, f"unknown speaker {sp!r}")
    text = accentuate(r.text, r.lang.lower()) if r.accent else r.text
    asterisk = r.format.lower() == "asterisk"
    sr = 8000 if asterisk else (r.sample_rate if r.sample_rate in (8000, 24000, 48000) else 48000)
    try:
        audio = model.apply_tts(text=text, speaker=sp, sample_rate=sr)
    except Exception as exc:
        log.exception("synthesis failed")
        raise HTTPException(500, f"synthesis failed: {exc}")
    data = audio.numpy() if hasattr(audio, "numpy") else audio
    if asterisk:
        import numpy as np
        peak = float(np.max(np.abs(data))) or 1.0
        data = data * (0.7079 / peak)
    buf = io.BytesIO()
    sf.write(buf, data, sr, format="WAV", subtype="PCM_16")
    return Response(content=buf.getvalue(), media_type="audio/wav")
