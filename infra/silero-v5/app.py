import logging
import io
import re
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


# ── Custom rules (admin-editable, persisted to /data/rules.json) ────────────
# Three rule types, applied around the accentuator:
#   abbr   — token → expansion, before everything ("тех." → "технический")
#   pron   — stem respell, before accentuation ("претензи" → "претэнзи")
#   stress — word → +marked form, AFTER accentuation (overrides ruaccent)
import json as _json
import os
RULES_PATH = "/data/rules.json"
CUSTOM: dict = {"abbr": {}, "pron": {}, "stress": {}}


def _load_rules():
    global CUSTOM
    try:
        if os.path.exists(RULES_PATH):
            with open(RULES_PATH) as f:
                data = _json.load(f)
            CUSTOM = {k: dict(data.get(k) or {}) for k in ("abbr", "pron", "stress")}
            log.info("custom rules loaded: %s", {k: len(v) for k, v in CUSTOM.items()})
    except Exception:
        log.exception("failed to load %s", RULES_PATH)


def _save_rules():
    os.makedirs(os.path.dirname(RULES_PATH), exist_ok=True)
    with open(RULES_PATH, "w") as f:
        _json.dump(CUSTOM, f, ensure_ascii=False, indent=1)


_load_rules()


def _case_repl(src: str, dst: str) -> str:
    return dst.capitalize() if src[:1].isupper() else dst


def _apply_abbr(text: str) -> str:
    for abbr, full in CUSTOM["abbr"].items():
        text = re.sub(r"(?<![\wа-яёіїєґА-ЯЁІЇЄҐ])" + re.escape(abbr) + r"(?![\wа-яёіїєґА-ЯЁІЇЄҐ])",
                      lambda m: _case_repl(m.group(0), full), text, flags=re.IGNORECASE)
    return text


def _apply_custom_pron(text: str) -> str:
    for stem, repl in CUSTOM["pron"].items():
        text = re.sub(r"\b" + re.escape(stem),
                      lambda m: _case_repl(m.group(0), repl), text, flags=re.IGNORECASE)
    return text


def _apply_stress_overrides(text: str) -> str:
    """Replace a word (with or without accentuator's + marks) by the admin's
    +marked form. «коротко» / «кор+отко» / «коротк+о» → configured mark."""
    for word, marked in CUSTOM["stress"].items():
        bare = word.replace("+", "")
        pat = r"\b" + r"\+?".join(re.escape(ch) for ch in bare) + r"\b"
        text = re.sub(pat, lambda m: _case_repl(m.group(0).replace("+", ""), marked),
                      text, flags=re.IGNORECASE)
    return text


# ── Pronunciation fixes (ru loanwords with hard э) ──────────────────────────
# The model reads spelling literally: "синтез" comes out as "синтЕз" while
# everyone says "синтЭз". Respell known loanword stems before accentuation
# (declension endings survive because we replace the stem only).
_RU_PRON_FIXES = {
    "синтез": "синтэз",
    "претензи": "претэнзи",
    "тезис": "тэзис",
    "менеджер": "мэнэджер",
    "бизнес": "бизнэс",
    "сервер": "сэрвер",
    "модем": "модэм",
    "тест": "тэст",
    "роутер": "роутэр",
    "компьютер": "компьютэр",
    "интернет": "интэрнэт",
    "детектор": "дэтэктор",
    "термин": "тэрмин",
}
_RU_PRON_RE = re.compile(
    r"\b(" + "|".join(sorted(_RU_PRON_FIXES, key=len, reverse=True)) + r")",
    re.IGNORECASE,
)


def _ru_pron_fix(text: str) -> str:
    def repl(m):
        src = m.group(1)
        dst = _RU_PRON_FIXES[src.lower()]
        return dst.capitalize() if src[0].isupper() else dst
    return _RU_PRON_RE.sub(repl, text)


def accentuate(text: str, lang: str) -> str:
    if "+" in text:
        return text  # caller already marked stress — trust them
    text = _apply_abbr(text)
    try:
        if lang == "ru":
            text = _ru_pron_fix(_apply_custom_pron(text))
            if RU_ACC is not None:
                text = RU_ACC.process_all(text)
            return _apply_stress_overrides(text)
        if lang in ("ua", "uk"):
            text = _apply_custom_pron(text)
            if UK_ACC is not None:
                text = _uk_to_plus(UK_ACC(text))
            return _apply_stress_overrides(text)
    except Exception as e:
        log.warning("accentuation failed (%s): %s — using raw text", lang, e)
    return text


import numpy as np

# VITS-class models degrade on long inputs (flattened intonation, pace drift).
# Synthesizing per sentence and joining with short pauses keeps long-text
# quality close to short-text quality.
_SENT_SPLIT = re.compile(r"(?<=[.!?…])\s+")
PAUSE_SEC = 0.18


def _split_sentences(text: str, max_len: int = 350) -> list[str]:
    parts = [p.strip() for p in _SENT_SPLIT.split(text.strip()) if p.strip()]
    out = []
    for p in parts:
        while len(p) > max_len:
            cut = p.rfind(",", 0, max_len)
            if cut < max_len // 3:
                cut = p.rfind(" ", 0, max_len)
            if cut <= 0:
                cut = max_len
            out.append(p[: cut + 1].strip())
            p = p[cut + 1:].strip()
        if p:
            out.append(p)
    # merge tiny fragments into the previous chunk
    merged = []
    for c in out:
        if merged and len(c) < 25:
            merged[-1] = (merged[-1] + " " + c).strip()
        else:
            merged.append(c)
    return merged or [text.strip()]


def _xml_escape(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _apply_one(text: str, speaker: str, sr: int, speed: float, pitch: str | None):
    """Single chunk synthesis; speed/pitch go through SSML prosody."""
    attrs = []
    if abs(speed - 1.0) > 0.01:
        attrs.append(f'rate="{int(speed * 100)}%"')
    if pitch and pitch != "medium":
        attrs.append(f'pitch="{pitch}"')
    if attrs:
        ssml = f"<speak><prosody {' '.join(attrs)}>{_xml_escape(text)}</prosody></speak>"
        a = model.apply_tts(ssml_text=ssml, speaker=speaker, sample_rate=sr)
    else:
        a = model.apply_tts(text=text, speaker=speaker, sample_rate=sr)
    return a.numpy() if hasattr(a, "numpy") else np.asarray(a)


def _synth(text: str, speaker: str, sr: int, speed: float = 1.0, pitch: str | None = None):
    """Per-sentence synthesis with short pauses; falls back gracefully."""
    sents = _split_sentences(text)
    if len(sents) == 1:
        return _apply_one(sents[0], speaker, sr, speed, pitch)
    pause = np.zeros(int(sr * PAUSE_SEC / max(speed, 0.5)), dtype="float32")
    pieces = []
    for s_ in sents:
        pieces.append(_apply_one(s_, speaker, sr, speed, pitch))
        pieces.append(pause)
    return np.concatenate(pieces[:-1])


app = FastAPI(title="Silero v5 cis_base TTS (MIT)")


class TTSReq(BaseModel):
    text: str
    lang: str = "ru"           # ru | ua (compat with v4 wrapper)
    speaker: str | None = None
    sample_rate: int = 48000   # 48k: noticeably better than 24k
    format: str = "wav"        # wav | mp3 | asterisk (8kHz mono 16-bit, normalized)
    accent: bool = True        # run the accentuator before synthesis
    speed: float = 1.0         # speech rate (0.5–2.0) via SSML prosody
    pitch: str | None = None   # x-low | low | medium | high | x-high


@app.get("/health")
def health():
    return {"status": "ok", "device": str(DEVICE), "speakers": len(SPEAKERS),
            "accent_ru": RU_ACC is not None, "accent_uk": UK_ACC is not None}


class RulesPayload(BaseModel):
    abbr: dict[str, str] = {}
    pron: dict[str, str] = {}
    stress: dict[str, str] = {}


@app.get("/rules")
def get_rules():
    return {"custom": CUSTOM, "builtin_pron": _RU_PRON_FIXES}


@app.put("/rules")
def put_rules(r: RulesPayload):
    global CUSTOM
    for d in (r.abbr, r.pron, r.stress):
        for k, v in d.items():
            if not k.strip() or len(k) > 100 or len(v) > 200:
                raise HTTPException(400, f"bad rule: {k!r}")
    CUSTOM = {"abbr": dict(r.abbr), "pron": dict(r.pron), "stress": dict(r.stress)}
    _save_rules()
    return {"saved": {k: len(v) for k, v in CUSTOM.items()}}


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
    speed = min(max(r.speed or 1.0, 0.5), 2.0)
    pitch = r.pitch if r.pitch in ("x-low", "low", "medium", "high", "x-high") else None
    try:
        data = _synth(text, sp, sr, speed, pitch)
    except Exception as exc:
        log.exception("synthesis failed")
        raise HTTPException(500, f"synthesis failed: {exc}")
    if asterisk:
        peak = float(np.max(np.abs(data))) or 1.0
        data = data * (0.7079 / peak)
    buf = io.BytesIO()
    if r.format.lower() == "mp3":
        # ~12x smaller than WAV — critical for time-to-first-audio over WAN.
        sf.write(buf, data, sr, format="MP3")
        return Response(content=buf.getvalue(), media_type="audio/mpeg")
    sf.write(buf, data, sr, format="WAV", subtype="PCM_16")
    return Response(content=buf.getvalue(), media_type="audio/wav")
