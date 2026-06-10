"""STT transcript normalizer.

After Whisper returns a raw transcript we run a post-processing pass that:
  1. Loads the tenant's domain vocabulary (streets, surnames, tech terms, …)
     from a configurable external source (SQL query, HTTP endpoint, …).
  2. Caches vocabulary per-tenant in memory (TTL = VOCAB_CACHE_TTL_SEC).
  3. Fuzzy-matches each word (and adjacent 2-word spans) in the transcript
     against the vocabulary and replaces confident matches.

Why post-processing instead of Whisper initial_prompt:
  - initial_prompt is limited to ~224 tokens — not enough for hundreds of
    street names from a billing database.
  - Post-processing is source-agnostic and can refresh automatically as the
    DB grows, without touching Whisper configuration.

Supported vocab source types (field stt_vocab_source in TenantShellConfig):
  {"type": "sql",  "query": "SELECT DISTINCT street FROM subscribers …"}
       → Uses stt_vocab_source_dsn_enc for the connection string.
  {"type": "http", "url": "https://…", "jq": ".streets[]"}
       → GETs the URL, extracts a list via the jq-like dot path.
  {"type": "tool", "tool_name": "…", "field": "…"}
       → Reserved for future implementation via tenant tool executor.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from typing import Any

logger = logging.getLogger(__name__)

# ── Cache ──────────────────────────────────────────────────────────────────────
VOCAB_CACHE_TTL_SEC: int = 3600  # 1 hour

# {tenant_id_str: {"terms": list[str], "ts": float}}
_vocab_cache: dict[str, dict[str, Any]] = {}
_vocab_cache_lock = asyncio.Lock()

# Minimum word length to attempt fuzzy correction (avoids matching "на", "у" etc.)
MIN_WORD_LEN = 4

# No hardcoded blacklist — language/domain-specific words to skip are configured
# per-tenant inside stt_vocab_source["blacklist"] (list of lowercase strings).
# Example for a Ukrainian ISP streets vocabulary:
#   "blacklist": ["проспект", "проспек", "вулиця", "бульвар", "площа", ...]

# Regex that matches a single "word token" in Cyrillic/Latin + digits
_TOKEN_RE = re.compile(r"[А-Яа-яЁёҐґЄєІіЇї\w]+", re.UNICODE)

# ── Address-fraction fix ──────────────────────────────────────────────────────
# Whisper normalizes "26/1" → "26.1" because it treats the slash as a division
# symbol and outputs a decimal. We detect and revert this for Cyrillic-heavy text.
#
# Heuristic: digit(s).single-digit where
#   • NOT preceded by a letter (excludes versions like "v3.5", "api2.0")
#   • NOT preceded/followed by another "." + digit (excludes IP / multi-part ver)
#   • NOT followed by a measurement unit (excludes "21.65 мс", "100.5 МБіт")
#   • Second part is a single digit 0-9 (address fractions are almost always N/1..N/9)
#
# Units that indicate a real decimal, not an address fraction:
_UNIT_PAT = (
    r"(?:мс|мілісекунд|секунд|сек|хвилин|хв|годин|год"
    r"|мб|мбіт|мбайт|гб|гбайт|тб|кб|кбайт|гц|мгц|ггц|кгц"
    r"|відс|грн|uah|usd|eur|mbit|mbps|kbps|gb|mb|kb"
    r"|мм|см|км|кг|г|мл|літр|вт|квт|ма|ампер|вольт|ом)"
)
_ADDR_FRAC_RE = re.compile(
    r"(?<![a-zA-Zа-яА-ЯёЁіїєґ.\d])"    # not preceded by letter or .digit
    r"(\d{1,4})"                          # house / street number  1..9999
    r"\."
    r"([0-9])"                            # single-digit apartment/entry (0-9)
    r"(?![.\d])"                          # not followed by .digit
    r"(?!\s*" + _UNIT_PAT + r")",         # not followed by measurement unit
    re.IGNORECASE,
)


def fix_address_fractions(text: str) -> str:
    """Revert Whisper's N.M → N/M normalisation for Ukrainian/Russian addresses.

    Only single-digit fractional parts (0-9) are converted; longer decimals
    (e.g. "21.65", "3.14") are left untouched.  Measurement units after the
    number also prevent conversion so "100.5 Мбіт" stays as-is.
    """
    return _ADDR_FRAC_RE.sub(r"\1/\2", text)


# ── Vocabulary loading ─────────────────────────────────────────────────────────

async def _load_from_sql(query: str, dsn: str) -> list[str]:
    """Run an async SQL query and return a flat list of non-empty strings."""
    parsed = dsn.strip()
    if parsed.startswith("mysql"):
        return await _load_sql_mysql(query, dsn)
    # asyncpg for Postgres
    import asyncpg
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(query)
        return [str(r[0]).strip() for r in rows if r[0] and str(r[0]).strip()]
    finally:
        await conn.close()


async def _load_sql_mysql(query: str, dsn: str) -> list[str]:
    """aiomysql variant — handles mysql:// and mysql+aiomysql:// DSN formats."""
    import aiomysql
    import re as _re

    # Parse DSN: mysql[+aiomysql]://user:pass@host[:port]/dbname
    m = _re.match(
        r"mysql(?:\+aiomysql)?://([^:]+):([^@]*)@([^:/]+)(?::(\d+))?/(.+)",
        dsn,
    )
    if not m:
        raise ValueError(f"Cannot parse MySQL DSN: {dsn!r}")
    user, password, host, port_s, db = m.groups()
    port = int(port_s) if port_s else 3306

    conn = await aiomysql.connect(
        host=host, port=port, user=user, password=password, db=db,
        charset="utf8mb4", autocommit=True,
    )
    try:
        async with conn.cursor() as cur:
            await cur.execute(query)
            rows = await cur.fetchall()
        return [str(r[0]).strip() for r in rows if r[0] and str(r[0]).strip()]
    finally:
        conn.close()


async def _load_from_http(url: str, jq_path: str | None) -> list[str]:
    """GET url, extract list of strings via a simple dot-path (no real jq needed)."""
    import httpx

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()

    # Walk the dot-path like ".streets[]" or "data.items"
    if jq_path:
        path = jq_path.lstrip(".").rstrip("[]")
        for key in path.split("."):
            if key and isinstance(data, dict):
                data = data.get(key, [])
    if isinstance(data, list):
        return [str(x).strip() for x in data if x and str(x).strip()]
    return []


async def _fetch_vocab(source: dict, dsn_enc: str | None) -> list[str]:
    """Dispatch to the right loader based on source["type"]."""
    from app.core.security import decrypt_value

    src_type = (source.get("type") or "").lower()

    if src_type == "sql":
        query = source.get("query", "")
        if not query:
            raise ValueError("stt_vocab_source.query is required for type=sql")
        if not dsn_enc:
            raise ValueError("stt_vocab_source_dsn_enc is required for type=sql")
        dsn = decrypt_value(dsn_enc)
        return await _load_from_sql(query, dsn)

    if src_type == "http":
        url = source.get("url", "")
        if not url:
            raise ValueError("stt_vocab_source.url is required for type=http")
        return await _load_from_http(url, source.get("jq"))

    if src_type == "tool":
        # Placeholder — implement via tenant tool executor if needed
        logger.warning("stt_vocab_source type=tool is not yet implemented")
        return []

    raise ValueError(f"Unknown stt_vocab_source type: {src_type!r}")


async def get_tenant_vocab(
    tenant_id: uuid.UUID,
    source: dict | None,
    dsn_enc: str | None,
    *,
    force_refresh: bool = False,
) -> list[str]:
    """Return cached (or freshly loaded) vocabulary for a tenant.

    Returns an empty list if no source is configured or loading fails.
    Thread-safe via asyncio lock.
    """
    if not source:
        return []

    key = str(tenant_id)
    now = time.monotonic()

    async with _vocab_cache_lock:
        cached = _vocab_cache.get(key)
        if (
            not force_refresh
            and cached is not None
            and (now - cached["ts"]) < VOCAB_CACHE_TTL_SEC
        ):
            return cached["terms"]

    # Load outside the lock so we don't block other tenants
    try:
        terms = await _fetch_vocab(source, dsn_enc)
        logger.info(
            "stt_normalizer: loaded %d vocab terms for tenant %s (type=%s)",
            len(terms), key, source.get("type"),
        )
    except Exception as exc:
        logger.warning(
            "stt_normalizer: failed to load vocab for tenant %s: %s", key, exc
        )
        # Keep stale cache if available; otherwise return empty
        async with _vocab_cache_lock:
            cached = _vocab_cache.get(key)
            return cached["terms"] if cached else []

    async with _vocab_cache_lock:
        _vocab_cache[key] = {"terms": terms, "ts": now}

    return terms


def invalidate_vocab_cache(tenant_id: uuid.UUID) -> None:
    """Drop cached vocabulary for a tenant (call after source config changes)."""
    _vocab_cache.pop(str(tenant_id), None)


# ── Normalization ──────────────────────────────────────────────────────────────

# Cyrillic word extractor — captures words 4+ chars, ignores noise like "пр.", "вул."
_WORD_EXTRACT_RE = re.compile(r'[А-ЯҐЄІЇа-яґєії\w]{4,}', re.UNICODE)
# Parenthetical old-name pattern: "Нова (Стара)" → extract both "Нова" and "Стара"
_PAREN_RE = re.compile(r'\(([^)]+)\)')


def _expand_street_terms(terms: list[str]) -> list[str]:
    """Expand a list of full street names into a flat vocabulary of matchable tokens.

    Examples:
      "Дубки (Косарева)"          → ["Дубки (Косарева)", "Дубки", "Косарева"]
      "Святителя Василя Великого" → full name + "Святителя", "Василя", "Великого"
      "Мира пр."                  → full name + "Мира"

    This lets the fuzzy matcher hit individual words from speech ("косарова" → "Косарева")
    even when the DB stores the full compound name.
    """
    result: list[str] = []
    seen: set[str] = set()

    def _add(s: str) -> None:
        if s and len(s) >= MIN_WORD_LEN and s not in seen:
            seen.add(s)
            result.append(s)

    for term in terms:
        _add(term)

        # Words inside parentheses (old Soviet names, alternative spellings)
        for paren_content in _PAREN_RE.findall(term):
            for w in _WORD_EXTRACT_RE.findall(paren_content):
                _add(w)

        # Strip parens, then individual words
        bare = _PAREN_RE.sub(' ', term)
        for w in _WORD_EXTRACT_RE.findall(bare):
            _add(w)

    return result


def _build_vocab_index(
    terms: list[str],
    blacklist: frozenset[str] = frozenset(),
) -> dict[str, str]:
    """Build a lowercase → canonical mapping for fuzzy matching.

    Input is the raw list from the DB (may contain multi-word / compound names).
    We expand each entry into individual matchable tokens first so that a single
    spoken word ("косарова") can match against an extracted component ("косарева")
    rather than needing to match the full "Дубки (Косарева)".

    blacklist: lowercase strings to exclude from the index so they are never
    used as replacement targets (e.g. street-type prefixes like "проспект"
    that appear as short extracted tokens and cause false positives).
    """
    expanded = _expand_street_terms(terms)
    index: dict[str, str] = {}
    for term in expanded:
        key = term.lower()
        if key and len(key) >= MIN_WORD_LEN and key not in blacklist:
            # Canonical = title-cased individual word (for clean replacement)
            index[key] = term if len(term.split()) > 1 else term.title()
    return index


def normalize_transcript(
    text: str,
    vocab: list[str],
    threshold: float = 85.0,
    blacklist: frozenset[str] = frozenset(),
) -> str:
    """Fuzzy-correct a Whisper transcript using a domain vocabulary.

    Algorithm:
      1. Tokenise text into (token, span) pairs, preserving punctuation/spaces.
      2. For each token >= MIN_WORD_LEN, try rapidfuzz against vocab index keys.
      3. If best_score >= threshold: replace with canonical vocab form.
      4. Reconstruct the string preserving original spacing/punctuation.

    Multi-word terms: we also try adjacent bigrams (token_i + token_i+1) so
    "свиче косарева" can match "вул. Косарева" style entries.

    blacklist: lowercase strings passed through to _build_vocab_index so those
    tokens are excluded from replacement candidates entirely.
    """
    if not vocab or not text:
        return text

    from rapidfuzz import process, fuzz

    vocab_index = _build_vocab_index(vocab, blacklist)
    if not vocab_index:
        return text

    vocab_keys = list(vocab_index.keys())

    # Tokenise: list of (token_str, start_pos, end_pos) or (None, start, end) for gaps
    tokens: list[tuple[str | None, int, int]] = []
    last = 0
    for m in _TOKEN_RE.finditer(text):
        if m.start() > last:
            tokens.append((None, last, m.start()))  # gap (space, punctuation)
        tokens.append((m.group(), m.start(), m.end()))
        last = m.end()
    if last < len(text):
        tokens.append((None, last, len(text)))

    # Extract word-only tokens with their indices
    word_positions = [(i, tok) for i, (tok, _, _) in enumerate(tokens) if tok is not None]

    replacements: dict[int, str] = {}

    i = 0
    while i < len(word_positions):
        pos, word = word_positions[i]
        if len(word) < MIN_WORD_LEN:
            i += 1
            continue

        word_lc = word.lower()

        # Try bigram first (current + next word) if next token exists
        matched = False
        if i + 1 < len(word_positions):
            next_pos, next_word = word_positions[i + 1]
            if len(next_word) >= MIN_WORD_LEN:
                bigram_lc = f"{word_lc} {next_word.lower()}"
                result = process.extractOne(
                    bigram_lc, vocab_keys, scorer=fuzz.ratio, score_cutoff=threshold
                )
                if result:
                    canonical = vocab_index[result[0]]
                    parts = canonical.split(None, 1)
                    replacements[pos] = parts[0]
                    if len(parts) > 1:
                        replacements[next_pos] = parts[1]
                    i += 2
                    matched = True

        if not matched:
            result = process.extractOne(
                word_lc, vocab_keys, scorer=fuzz.ratio, score_cutoff=threshold
            )
            if result:
                replacements[pos] = vocab_index[result[0]]
            i += 1

    if not replacements:
        return text

    # Reconstruct
    parts = []
    for idx, (tok, start, end) in enumerate(tokens):
        if tok is None:
            parts.append(text[start:end])
        elif idx in replacements:
            parts.append(replacements[idx])
        else:
            parts.append(tok)

    result_text = "".join(parts)
    if result_text != text:
        logger.debug("stt_normalizer: '%s' → '%s'", text, result_text)
    return result_text
