"""
Argument format pipeline.

A *format* is a single string describing a chain of transformations that
turn whatever the model produced into the exact bytes the backend wants.
Tenant-agnostic: this module only knows generic ops (case folding, regex,
template normalization, number parsing). Tenants compose them per tool
field in `enum_values[].formats[<alias>]`.

Pipeline syntax:
  ``op | op:arg | op:arg | ...``

Steps are separated by ``|`` (with optional surrounding whitespace).
Each step is ``name`` or ``name:arg``; ``arg`` runs to the next ``|``.

Available ops:
  lower                 → str.lower()
  upper                 → str.upper()
  trim                  → str.strip()
  template:TPL          → keep only data chars, refill into TPL
                          (x=hex lower, X=hex upper, 9=decimal)
  re:PATTERN            → validate full match, return value unchanged
                          (alias: validate)
  re_sub:PAT=>REPL      → re.sub on first matching group, returns the
                          rewritten string. Pattern may use \\1 \\2 backrefs.
                          Mismatch → error (so a typo doesn't pass silently).
  extract:KIND          → keep chars of KIND only. KIND ∈ hex digits alnum
  int                   → strip non-digits, parse int, str(int)
  pad_left:N[,CHAR]     → left-pad result to width N with CHAR (default '0')
  default:VAL           → if input empty/None, substitute VAL
  validate:PATTERN      → same as `re`

Backwards compatibility:
  ``xxxx.xxxx.xxxx``    → treated as ``template:xxxx.xxxx.xxxx``
  ``re:^...$``          → already a valid pipeline step

If a step returns None (template can't normalize, re doesn't match, etc),
pipeline aborts and returns an error string the executor surfaces to the
LLM as a tool error.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Callable

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────
# Template normalization (used by the `template:` op)
# ──────────────────────────────────────────────────────────────────────

_PLACEHOLDER_CLASS = {
    "x": "0123456789abcdefABCDEF",
    "X": "0123456789abcdefABCDEF",
    "9": "0123456789",
}


def _is_placeholder(ch: str) -> bool:
    return ch in _PLACEHOLDER_CLASS


def _template_to_regex(tpl: str) -> re.Pattern | None:
    parts: list[str] = []
    for ch in tpl:
        cls = _PLACEHOLDER_CLASS.get(ch)
        if cls is not None:
            parts.append(f"[{cls}]")
        else:
            parts.append(re.escape(ch))
    try:
        return re.compile("^" + "".join(parts) + "$")
    except re.error:
        return None


def _normalize_with_template(value: str, tpl: str) -> str | None:
    placeholders = [ch for ch in tpl if _is_placeholder(ch)]
    if not placeholders:
        return value if value == tpl else None
    allowed = set("".join(_PLACEHOLDER_CLASS[p] for p in placeholders))
    data = [ch for ch in value if ch in allowed]
    if len(data) != len(placeholders):
        return None
    out: list[str] = []
    di = 0
    for ch in tpl:
        if _is_placeholder(ch):
            src = data[di]
            di += 1
            if ch == "x":
                out.append(src.lower())
            elif ch == "X":
                out.append(src.upper())
            else:
                out.append(src)
        else:
            out.append(ch)
    return "".join(out)


# ──────────────────────────────────────────────────────────────────────
# Op registry
# ──────────────────────────────────────────────────────────────────────

OpFn = Callable[[str, str | None], "OpResult"]


@dataclass
class OpResult:
    value: str | None       # new value, or None on failure
    error: str | None = None  # human-readable error (Russian) when failed


def _op_lower(value: str, arg: str | None) -> OpResult:
    return OpResult(value.lower())


def _op_upper(value: str, arg: str | None) -> OpResult:
    return OpResult(value.upper())


def _op_trim(value: str, arg: str | None) -> OpResult:
    return OpResult(value.strip())


def _op_template(value: str, arg: str | None) -> OpResult:
    if not arg:
        return OpResult(None, "template: пустой аргумент (нужно `template:xxxx.xxxx.xxxx`)")
    rx = _template_to_regex(arg)
    if rx is None:
        return OpResult(None, f"template: невалидный шаблон {arg!r}")
    if rx.fullmatch(value):
        return OpResult(value)
    normalized = _normalize_with_template(value, arg)
    if normalized is None or not rx.fullmatch(normalized):
        return OpResult(None, f"template: {value!r} не приводится к {arg!r}")
    return OpResult(normalized)


def _op_validate(value: str, arg: str | None) -> OpResult:
    if not arg:
        return OpResult(None, "validate: нужен regex-аргумент")
    try:
        if re.fullmatch(arg, value):
            return OpResult(value)
        return OpResult(None, f"validate: {value!r} не матчит regex {arg!r}")
    except re.error as e:
        return OpResult(None, f"validate: невалидный regex {arg!r} — {e}")


def _op_re_sub(value: str, arg: str | None) -> OpResult:
    if not arg or "=>" not in arg:
        return OpResult(None, "re_sub: ожидается `re_sub:PATTERN=>REPLACEMENT`")
    pattern, _, repl = arg.partition("=>")
    try:
        if not re.search(pattern, value):
            return OpResult(None, f"re_sub: {value!r} не матчит {pattern!r}")
        return OpResult(re.sub(pattern, repl, value))
    except re.error as e:
        return OpResult(None, f"re_sub: невалидный regex {pattern!r} — {e}")


def _op_extract(value: str, arg: str | None) -> OpResult:
    arg = (arg or "").strip().lower()
    if arg == "hex":
        return OpResult("".join(ch for ch in value if ch in "0123456789abcdefABCDEF"))
    if arg == "digits":
        return OpResult("".join(ch for ch in value if ch.isdigit()))
    if arg == "alnum":
        return OpResult("".join(ch for ch in value if ch.isalnum()))
    return OpResult(None, f"extract: неизвестный KIND {arg!r} (доступны: hex, digits, alnum)")


def _op_int(value: str, arg: str | None) -> OpResult:
    digits = "".join(ch for ch in value if ch.isdigit() or (ch == "-" and not arg))
    try:
        return OpResult(str(int(digits)))
    except ValueError:
        return OpResult(None, f"int: {value!r} не парсится в число")


def _op_pad_left(value: str, arg: str | None) -> OpResult:
    if not arg:
        return OpResult(None, "pad_left: нужен аргумент `pad_left:N` или `pad_left:N,CHAR`")
    parts = arg.split(",", 1)
    try:
        width = int(parts[0].strip())
    except ValueError:
        return OpResult(None, f"pad_left: первый аргумент должен быть числом, дано {parts[0]!r}")
    ch = parts[1] if len(parts) > 1 and parts[1] else "0"
    if len(ch) != 1:
        return OpResult(None, f"pad_left: CHAR должен быть одним символом, дано {ch!r}")
    return OpResult(value.rjust(width, ch))


def _op_default(value: str, arg: str | None) -> OpResult:
    return OpResult(arg if value == "" else value)


_OPS: dict[str, OpFn] = {
    "lower": _op_lower,
    "upper": _op_upper,
    "trim": _op_trim,
    "template": _op_template,
    "re": _op_validate,            # alias of validate (back-compat)
    "validate": _op_validate,
    "re_sub": _op_re_sub,
    "extract": _op_extract,
    "int": _op_int,
    "pad_left": _op_pad_left,
    "default": _op_default,
}


def available_ops() -> list[str]:
    return sorted(_OPS.keys())


# ──────────────────────────────────────────────────────────────────────
# Pipeline parser
# ──────────────────────────────────────────────────────────────────────

@dataclass
class Step:
    op: str
    arg: str | None


def parse_pipeline(raw: str) -> list[Step]:
    """Parse a pipeline string into ordered steps.

    Backwards compat: a raw string that contains no `|` AND no known op
    prefix is treated as `template:<raw>` — that's how older tenants
    stored MAC formats."""
    if not isinstance(raw, str):
        return []
    s = raw.strip()
    if not s:
        return []

    if "|" not in s:
        # Heuristic: pipeline-aware string vs legacy bare template
        head = s.split(":", 1)[0].strip().lower()
        if head not in _OPS:
            return [Step(op="template", arg=s)]

    steps: list[Step] = []
    for raw_step in s.split("|"):
        chunk = raw_step.strip()
        if not chunk:
            continue
        if ":" in chunk:
            name, _, arg = chunk.partition(":")
            steps.append(Step(op=name.strip().lower(), arg=arg))
        else:
            steps.append(Step(op=chunk.lower(), arg=None))
    return steps


def apply_pipeline(value, raw_format: str) -> tuple[object, str | None]:
    """Run the pipeline. Returns (final_value, error). On success error is
    None and final_value is what to pass downstream (mutated or not). On
    failure returns the *original* value and the error string."""
    if value is None:
        return value, None
    steps = parse_pipeline(raw_format)
    if not steps:
        return value, None

    if not isinstance(value, str):
        # Coerce to string so ops can work; safe because the schema
        # validator runs after us and will reject if type was wrong.
        cur = str(value)
    else:
        cur = value

    for step in steps:
        fn = _OPS.get(step.op)
        if fn is None:
            return value, f"format pipeline: неизвестная операция {step.op!r}"
        result = fn(cur, step.arg)
        if result.error:
            return value, (f"format pipeline шаг {step.op!r}: {result.error}")
        if result.value is None:
            return value, f"format pipeline шаг {step.op!r} вернул пусто"
        cur = result.value
    return cur, None


# ──────────────────────────────────────────────────────────────────────
# Public façade used by executor (kept stable across implementations)
# ──────────────────────────────────────────────────────────────────────

def normalize_or_validate(value, raw_format: str) -> tuple[object, str | None]:
    """Single entry point. Same signature the executor already calls."""
    return apply_pipeline(value, raw_format)
