"""Characterization tests for pure pipeline helpers.

These pin the behavior of the small, side-effect-free helpers that the big
_chat_completion_inner orchestrator depends on, so a future refactor that moves
them around has a fast safety net (no DB / no LLM server needed).
"""
from app.services.llm import pipeline as p


# ── _resolve_thinking_kwargs ────────────────────────────────────────────────
def _thinking(d):
    return None if d is None else d["chat_template_kwargs"]["enable_thinking"]


def test_thinking_off_mode_disables():
    assert _thinking(p._resolve_thinking_kwargs("off", "длинный вопрос про сеть", False)) is False


def test_thinking_voice_mode_forces_off():
    assert _thinking(p._resolve_thinking_kwargs("on", "x", False, voice_mode=True)) is False


def test_thinking_auto_disables_on_tool_round():
    assert _thinking(p._resolve_thinking_kwargs("auto", "достаточно длинный запрос " * 5, True)) is False


def test_thinking_auto_disables_on_short_query():
    assert _thinking(p._resolve_thinking_kwargs("auto", "привет", False)) is False


def test_thinking_auto_final_longform_uses_default():
    # long query, no tools, auto → model default (None)
    assert p._resolve_thinking_kwargs("auto", "это довольно длинный запрос на диагностику " * 3, False) is None


def test_thinking_on_longform_uses_default():
    assert p._resolve_thinking_kwargs("on", "длинный запрос " * 10, False) is None


# ── _resolve_max_tool_rounds ────────────────────────────────────────────────
class _Cfg:
    def __init__(self, v):
        self.max_tool_rounds = v


def test_max_tool_rounds_default_when_none():
    assert p._resolve_max_tool_rounds(_Cfg(None)) == p.DEFAULT_MAX_TOOL_ROUNDS


def test_max_tool_rounds_clamps_high():
    assert p._resolve_max_tool_rounds(_Cfg(999)) == 20


def test_max_tool_rounds_clamps_low():
    assert p._resolve_max_tool_rounds(_Cfg(0)) == 1


def test_max_tool_rounds_invalid_falls_back():
    assert p._resolve_max_tool_rounds(_Cfg("abc")) == p.DEFAULT_MAX_TOOL_ROUNDS


# ── _deterministic_compress ─────────────────────────────────────────────────
def test_compress_noop_when_short():
    assert p._deterministic_compress("short", keep_chars=100) == "short"


def test_compress_keeps_head_and_tail():
    content = "HEAD" + ("x" * 5000) + "TAIL"
    out = p._deterministic_compress(content, keep_chars=200)
    assert out.startswith("HEAD")
    assert out.endswith("TAIL")
    assert "сжато" in out
    assert len(out) < len(content)


# ── _clamp_temperature ──────────────────────────────────────────────────────
def test_clamp_temperature_default():
    assert p._clamp_temperature(None) == 0.3


def test_clamp_temperature_caps_at_max():
    assert p._clamp_temperature(5.0) == p.MAX_SAFE_TEMPERATURE


def test_clamp_temperature_floor():
    assert p._clamp_temperature(-1.0) == 0.0


# ── _normalize_context_mode ─────────────────────────────────────────────────
def test_context_mode_valid_passthrough():
    assert p._normalize_context_mode("recent_only") == "recent_only"


def test_context_mode_invalid_default():
    assert p._normalize_context_mode("garbage") == "summary_plus_recent"
    assert p._normalize_context_mode(None) == "summary_plus_recent"


# ── _with_language_system_tail ──────────────────────────────────────────────
def test_language_tail_appended_for_russian():
    msgs = [{"role": "user", "content": "x"}]
    out = p._with_language_system_tail(msgs, "Привет, не работает интернет дома совсем")
    assert len(out) == len(msgs) + 1
    assert out[-1]["role"] == "system"
    assert msgs == [{"role": "user", "content": "x"}]  # original not mutated


def test_language_tail_noop_for_english():
    msgs = [{"role": "user", "content": "x"}]
    out = p._with_language_system_tail(msgs, "Hello my internet is down at home please help")
    assert out == msgs


# ── _is_lazy_response ───────────────────────────────────────────────────────
def test_lazy_response_detects_intent():
    assert p._is_lazy_response("Сейчас проверю статус вашего подключения")


def test_lazy_response_false_on_plain_answer():
    assert not p._is_lazy_response("Ваш баланс составляет 150 гривен.")


# ── _build_datetime_block ───────────────────────────────────────────────────
class _TzCfg:
    def __init__(self, tz):
        self.timezone = tz


def test_datetime_block_includes_header():
    out = p._build_datetime_block(_TzCfg("Europe/Kyiv"), "cid")
    assert out is not None
    assert "Текущая дата и время" in out


def test_datetime_block_bad_tz_falls_back():
    # A bogus timezone must not raise — falls back to server local.
    out = p._build_datetime_block(_TzCfg("Not/AZone"), "cid")
    assert out is not None and "Сейчас" in out


# ── _ct (tiktoken token count) ──────────────────────────────────────────────
def test_ct_empty_is_zero():
    assert p._ct("") == 0
    assert p._ct(None) == 0


def test_ct_counts_tokens():
    assert p._ct("hello world") > 0
