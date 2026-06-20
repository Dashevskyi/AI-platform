"""
Tier 0 routing: deterministic shortcut for trivial queries.

When a query is unambiguous (high semantic match to a single tool + all
required entities extractable from the text), we call the tool directly and
render its output through a Jinja-like template — without ever invoking
the LLM. ~100-300ms vs 1-2s for the full pipeline.

Activation requirements (ALL must be true for Tier 0 to fire):
  1. Tenant has `tier0_enabled = True`
  2. Top semantic-matched tool has `x_backend_config.tier0_template` config
  3. Top tool's score >= `tier0_min_tool_score`
  4. Gap to second tool >= `tier0_max_score_gap` (no close competitor)
  5. All `required_entity` types are present in the query
  6. All `required_fields` in the rendered output are non-null

If ANY of those fail → returns None and pipeline falls through to LLM.

Per-tool config shape (in tenant_tools.config_json):
    "x_backend_config": {
      "tier0_template": {
        "template": "Клиент {name}, баланс {balance} грн.",
        "required_entity": "phone",
        "param_map": {"filters.phone": "$phone"},
        "required_fields": ["name", "balance"]
      }
    }

`required_entity` supported values:
    "phone"            — Ukrainian phone number in query
    "mac"              — MAC address in query
    "ip"               — IPv4 address in query
    "id"               — numeric ID (prefixed: #123, № 456, etc.)
    "email"            — email address in query
    "date"             — date in query (DD.MM.YYYY, ISO, or named Slavic month)
    "keyword_extract"  — arbitrary text captured by `keyword_regex` capture group
                         Useful for free-text like name/address/switch after a keyword.
                         Requires `keyword_regex` field in the same config dict.

`param_map` / `param_maps` values use $entity references:
    $phone              → first extracted phone
    $mac                → first extracted MAC
    $ip                 → first extracted IP
    $id                 → first extracted numeric ID
    $email              → first extracted email
    $date               → first extracted date (YYYY-MM-DD)
    $keyword_extract    → text captured by keyword_regex

    Pipe-separated format pipeline: "$phone|re_sub:^\\+38=>"
"""
from __future__ import annotations

import json
import logging
import re
import string
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.preprocessing.entities import ExtractedEntities, extract_entities
from app.services.tools.embedder import search_tools
from app.services.tools.executor import execute_tool

logger = logging.getLogger(__name__)


@dataclass
class Tier0Result:
    """Successful Tier 0 hit — answer was assembled without LLM."""
    content: str
    tool_name: str
    confidence: float
    second_score: float          # for transparency (gap-check explanation)
    latency_ms: float
    extracted_entities: dict
    # Raw tool call of the successful attempt — lets the pipeline promote the
    # result to a first-class artifact (same as the LLM tool loop does), so
    # follow-up turns can ground on it. None when the hit was a not-found
    # template (nothing worth grounding on).
    arguments: dict | None = None
    tool_output: str | None = None


def _entity_value(
    entities: ExtractedEntities,
    ref: str,
    extra: dict[str, str] | None = None,
) -> str | None:
    """Resolve $phone / $mac / $ip / $id / $email / $date / $keyword_extract
    reference to first matching entity.

    Supports a `|`-separated format-pipeline suffix that runs through the
    same `format_template` engine the executor uses for `arg_formats`:

        "$phone"                   → "+380501234567"
        "$phone|re_sub:^\\+38=>"   → "0501234567"
        "$mac|upper"               → "AABB.CCDD.EEFF"
        "$keyword_extract"         → text captured by keyword_regex
    """
    if not isinstance(ref, str) or not ref.startswith("$"):
        return ref  # literal value
    body = ref[1:]
    pipeline_suffix = ""
    if "|" in body:
        kind_part, pipeline_suffix = body.split("|", 1)
    else:
        kind_part = body
    kind = kind_part.lower()
    bag: dict[str, list[str]] = {
        "phone": entities.phones,
        "mac": entities.macs,
        "ip": entities.ips,
        "id": entities.numeric_ids,
        "email": entities.emails,
        "date": entities.dates,
        "keyword_extract": (
            [extra["keyword_extract"]]
            if extra and extra.get("keyword_extract")
            else []
        ),
    }
    vals = bag.get(kind, [])
    if not vals:
        return None
    value = vals[0]
    if pipeline_suffix:
        from app.services.tools.format_template import normalize_or_validate
        new_value, err = normalize_or_validate(value, pipeline_suffix)
        if err:
            logger.info("[tier0] format pipeline failed for %s: %s", ref, err)
            return None
        return new_value
    return value


def _set_at_path(obj: dict, dotted: str, value) -> None:
    parts = dotted.split(".")
    cur = obj
    for p in parts[:-1]:
        if p not in cur or not isinstance(cur[p], dict):
            cur[p] = {}
        cur = cur[p]
    cur[parts[-1]] = value


def _get_at_path(obj, dotted: str):
    """Walk dotted path through dicts AND lists ([0], [1] selectors)."""
    cur = obj
    for p in dotted.split("."):
        if cur is None:
            return None
        if isinstance(cur, list):
            try:
                cur = cur[int(p)]
                continue
            except (ValueError, IndexError):
                return None
        if isinstance(cur, dict):
            cur = cur.get(p)
        else:
            return None
    return cur


class _SafeFormatter(string.Formatter):
    """Renders `{a.b.c}` against a dict, returning the literal placeholder
    when a path is missing — so we can detect failure without crashing.

    Supports custom format specs:
      phones        → split concatenated 10-char phone strings "XXXXXXXXXX / XXXXXXXXXX"
      money         → strip ".0" from float (133.0 → 133)
      int           → convert to integer string
      table         → markdown table; column definitions from table_defs[field_name]
      table:c1,c2   → markdown table with explicit columns (uses table_defs for formatting)
    """

    MISSING = object()

    def __init__(self, table_defs: dict | None = None, missing_repr: str = "{MISSING}",
                 value_maps: dict | None = None):
        super().__init__()
        # table_defs: {field_name: {"columns": [{"field":…,"label":…,"values":…,…}]}}
        self._table_defs: dict = table_defs or {}
        # value_maps: {field_path_or_leaf: {raw_value: display}} for the `:map` spec
        self._value_maps: dict = value_maps or {}
        self._current_field: str = ""  # set by get_field before format_field
        self._missing_repr = missing_repr
        self.missing_count = 0  # how many placeholders resolved to MISSING

    def get_field(self, field_name: str, args, kwargs):
        self._current_field = field_name
        obj = kwargs.get("_root", {})
        val = _get_at_path(obj, field_name)
        if val is None:
            return self.MISSING, field_name
        return val, field_name

    # ------------------------------------------------------------------
    # Helpers for the "table" spec
    # ------------------------------------------------------------------
    @staticmethod
    def _render_cell(v, col_def: dict) -> str:
        """Apply a single column definition to a raw cell value."""
        import re as _re
        # Resolve raw value
        if v is None or v == "":
            v = col_def.get("empty", "")
            return str(v)
        # value map: {"up": "↑", "down": "↓"}
        val_map = col_def.get("values")
        if val_map and isinstance(val_map, dict):
            v = val_map.get(str(v), str(v))
        else:
            v = str(v)
        # format spec: "phones" or "money" (same logic as format_field)
        fmt = col_def.get("format", "")
        if fmt == "phones":
            s = v.strip()
            chunks = [s[i:i+10] for i in range(0, len(s), 10) if s[i:i+10].strip()]
            v = " / ".join(chunks) if chunks else s
        elif fmt == "money":
            try:
                f = float(v)
                v = str(int(f)) if f == int(f) else f"{f:.2f}"
            except (ValueError, TypeError):
                pass
        # strip_html
        if col_def.get("strip_html"):
            v = _re.sub(r"<[^>]+>", " ", v).strip()
        # prefix / suffix
        if v:
            if col_def.get("prefix"):
                v = str(col_def["prefix"]) + v
            if col_def.get("suffix"):
                v = v + str(col_def["suffix"])
        # empty fallback after transforms
        if not v.strip():
            v = col_def.get("empty", "")
        # max_len truncation
        max_len = col_def.get("max_len")
        if max_len and len(v) > max_len:
            v = v[:max_len - 1] + "…"
        return v

    def _render_table(self, value, col_defs: list[dict]) -> str:
        """Render list-of-dicts as a markdown table using col_defs."""
        if not isinstance(value, list) or not value:
            return "(нет данных)"
        headers = [cd.get("label", cd.get("field", "?")) for cd in col_defs]
        lines = [
            "| " + " | ".join(headers) + " |",
            "|" + "|".join(["---"] * len(col_defs)) + "|",
        ]
        for row in value:
            if not isinstance(row, dict):
                continue
            cells = [
                self._render_cell(row.get(cd["field"]), cd)
                for cd in col_defs
            ]
            lines.append("| " + " | ".join(cells) + " |")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    def format_field(self, value, format_spec):
        if value is self.MISSING:
            self.missing_count += 1
            return self._missing_repr

        # phones → split concatenated 10-char phone numbers
        if format_spec == "phones":
            s = str(value).strip()
            chunks = [s[i:i+10] for i in range(0, len(s), 10) if s[i:i+10].strip()]
            return " / ".join(chunks) if chunks else s

        # money → strip ".0" (133.0 → 133)
        if format_spec == "money":
            try:
                f = float(value)
                return str(int(f)) if f == int(f) else f"{f:.2f}"
            except (ValueError, TypeError):
                return str(value)

        # int → integer string
        if format_spec == "int":
            try:
                return str(int(float(value)))
            except (ValueError, TypeError):
                return str(value)

        # map → value_maps lookup: 1 → "Включен", "up" → "🟢", etc.
        # Keyed by full path ("items.0.state") or the leaf segment ("state").
        if format_spec == "map":
            fld = self._current_field
            m = self._value_maps.get(fld) or self._value_maps.get(fld.split(".")[-1])
            if isinstance(m, dict):
                return m.get(str(value), str(value))
            return str(value)

        # table / table:c1,c2  → markdown table
        #
        # Column definitions come from table_defs[current_field].columns.
        # If format_spec is "table:c1,c2", only those columns are shown
        # (matched by field name); unknown columns get a bare {"field":c} def.
        if format_spec == "table" or format_spec.startswith("table:"):
            tdef = self._table_defs.get(self._current_field, {})
            all_col_defs: list[dict] = tdef.get("columns", [])
            by_field = {cd["field"]: cd for cd in all_col_defs if "field" in cd}

            if format_spec.startswith("table:"):
                # Explicit column order overrides table_defs order
                explicit = [c.strip() for c in format_spec[6:].split(",") if c.strip()]
                col_defs = [by_field.get(c, {"field": c, "label": c}) for c in explicit]
            elif all_col_defs:
                col_defs = all_col_defs
            else:
                # No defs at all — auto-detect from first row
                if isinstance(value, list) and value and isinstance(value[0], dict):
                    col_defs = [{"field": k, "label": k} for k in value[0].keys()]
                else:
                    col_defs = []

            return self._render_table(value, col_defs)

        return super().format_field(value, format_spec)


def _render_template(
    template: str,
    data: dict,
    required_fields: list[str],
    table_defs: dict | None = None,
    value_maps: dict | None = None,
) -> str | None:
    """Render `{a.b}`-style placeholders. Returns None if any required field
    is missing — caller falls back to LLM in that case.

    table_defs — optional column-format definitions for the `table` spec:
      {
        "ports": {
          "columns": [
            {"field": "index", "label": "#"},
            {"field": "oper",  "label": "Ст", "values": {"up": "🟢", "down": "🔴"}},
            {"field": "comment", "label": "Комментарий", "max_len": 40, "strip_html": true}
          ]
        }
      }
    """
    for path in required_fields or []:
        if _get_at_path(data, path) in (None, ""):
            logger.info("[tier0] required field '%s' missing — falling back", path)
            return None
    try:
        fmt = _SafeFormatter(table_defs=table_defs, value_maps=value_maps)
        out = fmt.format(template, _root=data)
    except Exception:
        logger.exception("[tier0] template render failed")
        return None
    # Never emit half-rendered output with literal {MISSING} placeholders — that
    # means the data we expected wasn't there. Signal failure so the caller can
    # use not_found_template or fall through to the LLM.
    if fmt.missing_count and "{MISSING}" in out:
        logger.info("[tier0] template had %d missing field(s) — treating as not rendered", fmt.missing_count)
        return None
    return out


def _result_is_empty(data, required_fields: list[str]) -> bool:
    """Heuristic: did the tool succeed but return *no matching record*? Used to
    pick not_found_template over the normal template. Domain-agnostic."""
    if required_fields:
        if any(_get_at_path(data, p) in (None, "") for p in required_fields):
            return True
    if isinstance(data, list):
        return len(data) == 0
    if isinstance(data, dict):
        for key in ("items", "results", "data", "rows", "records", "list", "result", "matches"):
            v = data.get(key)
            if isinstance(v, list):
                return len(v) == 0
        return len(data) == 0
    return data in (None, "", [])


def _render_not_found(template: str, entities: ExtractedEntities,
                      keyword_extracted: str | None, user_query: str) -> str | None:
    """Render the 'no record found' template. Placeholders reference the query
    and extracted entities: {keyword_extract}, {phone}, {ip}, {mac}, {id},
    {email}, {date}, {query}. Unknown placeholders render empty (not {MISSING})."""
    root = {
        "keyword_extract": keyword_extracted or "",
        "query": user_query,
        "phone": (entities.phones or [""])[0],
        "mac": (entities.macs or [""])[0],
        "ip": (entities.ips or [""])[0],
        "id": (entities.numeric_ids or [""])[0],
        "email": (entities.emails or [""])[0],
        "date": (entities.dates or [""])[0],
    }
    try:
        return (_SafeFormatter(missing_repr="").format(template, _root=root)).strip() or None
    except Exception:
        logger.exception("[tier0] not_found_template render failed")
        return None


# Parameter-name synonyms by entity kind. Used by the entity-aware boost
# to detect "this tool natively accepts what we extracted from the text".
# Conservative on purpose — only obvious matches.
_ENTITY_PARAM_NAMES: dict[str, set[str]] = {
    "phone": {"phone", "sms_phone", "phone_number", "tel", "telephone", "mobile"},
    "mac": {"mac", "mac_address", "macaddr", "hwaddr", "mac_addr"},
    "ip": {"ip", "ip_address", "ipaddr", "ip_addr"},
    "email": {"email", "email_address", "mail", "e_mail"},
    "date": {"date", "date_from", "date_to", "start_date", "end_date", "created_at"},
}


def _walk_param_names(node) -> set[str]:
    """Collect every leaf property name in a JSON-schema-like parameters tree."""
    out: set[str] = set()
    if not isinstance(node, dict):
        return out
    props = node.get("properties")
    if isinstance(props, dict):
        for name, sub in props.items():
            out.add(str(name).lower())
            out |= _walk_param_names(sub)
    # arrays of items with their own schema
    items = node.get("items")
    if isinstance(items, dict):
        out |= _walk_param_names(items)
    return out


def _compute_entity_boost(
    tool,
    entities: ExtractedEntities,
    bonus: float = 0.20,
) -> tuple[float, list[str]]:
    """Boost a tool's score if it accepts the same entity kinds we extracted.

    Returns (max_bonus, matched_entity_kinds). Multiple matches don't stack —
    a tool that accepts both `phone` and `mac` and the query has both still
    gets just `bonus`, not 2× bonus (overweighting would harm calibration).
    """
    cfg = getattr(tool, "config_json", None) or {}
    fn = (cfg.get("function") or {})
    params = fn.get("parameters") or {}
    names = _walk_param_names(params)
    matched: list[str] = []
    for kind, synonyms in _ENTITY_PARAM_NAMES.items():
        bag_key = {"email": "emails", "date": "dates"}.get(kind, kind + "s")
        if bag_key == "ids":
            bag_key = "numeric_ids"
        entity_vals = entities.as_dict().get(bag_key, [])
        if entity_vals and (names & synonyms):
            matched.append(kind)
    return (bonus if matched else 0.0), matched


def _block_hit(block_keywords, user_query: str) -> str | None:
    """Word-boundary block-keyword match. Substring matching was a trap:
    block «на» fired inside «НАйди» and silently killed the right tool."""
    uq = user_query.lower()
    for bk in (block_keywords or []):
        if not bk:
            continue
        if re.search(r"(?<![\w\u0400-\u04FF])" + re.escape(bk.lower()) + r"(?![\w\u0400-\u04FF])", uq):
            return bk
    return None


def _extract_tier0_config(tool) -> dict | None:
    cfg = getattr(tool, "config_json", None) or {}
    runtime = cfg.get("x_backend_config")
    if not isinstance(runtime, dict):
        return None
    t0 = runtime.get("tier0_template")
    if not isinstance(t0, dict):
        return None
    # raw_output tools don't need a template — they pass the output verbatim.
    # All other tools must have a non-empty template for result rendering.
    if not t0.get("raw_output") and not t0.get("template"):
        return None
    return t0


async def try_tier0(
    user_query: str,
    tenant_id: str,
    db: AsyncSession,
    embedding_model: str | None,
    *,
    min_tool_score: float = 0.80,
    max_score_gap: float = 0.15,
    tool_context: dict | None = None,
    candidate_ids: "Sequence | None" = None,
) -> Tier0Result | None:
    """Run the Tier 0 router. Returns None to signal "fall back to LLM".

    `tool_context` is the per-request `_context` (actor, redact_fields, ids) the
    main pipeline injects into tool configs. Tier 0 MUST forward it too, or the
    deterministic path bypasses PII redaction and actor forced-filters.

    `candidate_ids` is the effective allow-set (API-key ∩ assistant) — Tier 0
    MUST only route to tools the current assistant can actually call, else it can
    short-circuit to an out-of-scope tool (e.g. an operator asking for
    "абоненти" matched tenant-wide `search_clients`), leaving the model with an
    empty catalog. None = no restriction (all tenant tools)."""
    if not (user_query and user_query.strip()):
        return None
    if not embedding_model:
        return None  # need embeddings to do semantic match
    if candidate_ids is not None and len(candidate_ids) == 0:
        return None  # assistant/key has no tool access — nothing for Tier 0 to do

    t_start = time.perf_counter()

    # Step 1 — extract strict-format entities (cheap regex, no I/O)
    entities = extract_entities(user_query)

    # Step 2 — semantic tool ranking.
    # Note: we no longer do an early exit on entities.has_any() here because
    # `keyword_extract` tools can fire on any query (no strict-format entity
    # needed). The entity check is deferred to step 5 (per tool config).
    try:
        top = await search_tools(
            tenant_id=str(tenant_id),
            query=user_query,
            db=db,
            embedding_model=embedding_model,
            candidate_ids=candidate_ids,
            top_k=5,
        )
    except Exception:
        logger.exception("[tier0] semantic search failed; fallback to LLM")
        return None
    if not top:
        return None

    # Entity-aware boost: a tool that natively accepts what we extracted
    # (filters.phone for a phone query) gets +0.20.
    boosted: list[tuple[Any, float, float, float, list[str]]] = []
    for t in top:
        raw = float(getattr(t, "_semantic_score", 0.0) or 0.0)
        bonus, matched = _compute_entity_boost(t, entities)
        boosted.append((t, raw + bonus, raw, bonus, matched))
    boosted.sort(key=lambda x: -x[1])

    # Pick the top tool that is actually Tier-0-eligible (has tier0_template).
    eligible = [b for b in boosted if _extract_tier0_config(b[0]) is not None]
    if not eligible:
        logger.debug("[tier0] no tier0_template tool in top-K — fallback")
        return None

    top_tool, top_score, top_raw, top_bonus, top_matched = eligible[0]
    competitors = [b for b in boosted if b[0].id != top_tool.id]
    second_score = competitors[0][1] if competitors else 0.0

    if top_bonus > 0:
        logger.info(
            "[tier0] entity boost: %s +%.2f for matched=%s (raw=%.3f → %.3f)",
            top_tool.name, top_bonus, top_matched, top_raw, top_score,
        )

    # Step 3 — confidence gates
    #
    # keyword_extract tools get a two-tier gate:
    #   Tier A (regex-first): scan ALL eligible keyword_extract tools in score
    #     order. If a tool's keyword_regex matches the query AND its score
    #     passes a sanity floor (0.25), it wins — deterministic rule trumps
    #     soft semantic confidence. The sanity floor prevents a completely
    #     unrelated tool whose regex accidentally matches from firing.
    #   Tier B (semantic): for non-keyword_extract tools (or when no regex
    #     matches), apply the normal score/gap gate.
    #
    # Rationale: address / name queries like "покажи клиентов косарева 26"
    # dilute the embedding (proper noun suffix shifts the vector away from the
    # client-search cluster) so the semantic score is often 0.35-0.45 even
    # when the intent is crystal-clear. The keyword_regex is the reliable
    # intent signal in those cases.
    REGEX_SANITY_FLOOR = 0.25

    keyword_extracted: str | None = None  # may be pre-set by regex bypass below
    t0_cfg: dict | None = None

    # Scan eligible keyword_extract tools for a regex match (highest score first).
    for b_tool, b_score, b_raw, b_bonus, b_matched in eligible:
        b_t0 = _extract_tier0_config(b_tool)
        if not b_t0 or b_t0.get("required_entity") != "keyword_extract":
            continue
        if b_score < REGEX_SANITY_FLOOR:
            logger.debug(
                "[tier0] keyword_extract tool %s below sanity floor (%.3f)",
                b_tool.name, b_score,
            )
            continue
        kw_regex = b_t0.get("keyword_regex") or ""
        if not kw_regex:
            continue
        # Block Tier 0 if query contains any of the configured block_keywords.
        # Use case: "покажи клиентів з тарифом 50 грн" — "з тарифом" signals
        # a conditional query that LLM must handle, not Tier 0.
        block_keywords = b_t0.get("block_keywords") or []
        if _block_hit(block_keywords, user_query):
            logger.debug(
                "[tier0] block_keywords hit for %s — skipping Tier 0", b_tool.name
            )
            continue
        m_pre = re.match(kw_regex, user_query, re.IGNORECASE)
        if m_pre and m_pre.lastindex and m_pre.lastindex >= 1:
            extracted = m_pre.group(1).strip()
            # Strip configurable context prefixes (e.g. "на свиче", "по свичу").
            # Defined as tier0_template.strip_prefixes — list of strings to
            # strip (case-insensitive) from the start of the captured keyword.
            strip_prefixes = b_t0.get("strip_prefixes") or []
            for sp in strip_prefixes:
                if sp and extracted.lower().startswith(sp.lower()):
                    extracted = extracted[len(sp):].strip()
                    break
            if extracted:
                # Regex fired — this tool wins. Override top_tool and bypass
                # the normal score/gap gate entirely.
                top_tool = b_tool
                top_score = b_score
                t0_cfg = b_t0
                keyword_extracted = extracted
                logger.info(
                    "[tier0] keyword_extract pre-match: %s (score=%.3f) → bypass score gate",
                    b_tool.name, b_score,
                )
                break

    if t0_cfg is None:
        # No keyword_extract tool matched via regex — fall back to semantic gate.
        # Re-establish top_tool (may have been overridden in the loop above).
        top_tool, top_score, top_raw, top_bonus, top_matched = eligible[0]
        competitors = [b for b in boosted if b[0].id != top_tool.id]
        second_score = competitors[0][1] if competitors else 0.0

        if top_score < min_tool_score:
            logger.info("[tier0] top score %.3f < min %.3f", top_score, min_tool_score)
            return None
        if (top_score - second_score) < max_score_gap:
            logger.info(
                "[tier0] gap too small: top=%.3f second=%.3f (need >=%.3f)",
                top_score, second_score, max_score_gap,
            )
            return None

        # Step 4 — fetch tier0 config (not yet set because no regex bypass)
        t0_cfg = _extract_tier0_config(top_tool)

    # Step 5 — entity presence check
    required_entity = t0_cfg.get("required_entity")

    if required_entity:
        kind = str(required_entity).lower()

        if kind == "keyword_extract":
            if not keyword_extracted:
                # Should only reach here if regex bypass did NOT fire
                # (e.g., the top tool's required_entity is keyword_extract
                # but we got here via the semantic gate — unlikely but safe).
                kw_regex = t0_cfg.get("keyword_regex") or ""
                if kw_regex:
                    m = re.match(kw_regex, user_query, re.IGNORECASE)
                    if m and m.lastindex and m.lastindex >= 1:
                        keyword_extracted = m.group(1).strip()
                if not keyword_extracted:
                    logger.info(
                        "[tier0] keyword_extract: pattern %r → no match in query",
                        kw_regex,
                    )
                    return None
        else:
            # Standard entity types — bail if the required kind is absent.
            # Map entity kind → as_dict() key
            plural_map = {
                "id": "numeric_ids",
                "email": "emails",
                "date": "dates",
                "phone": "phones",
                "mac": "macs",
                "ip": "ips",
            }
            bag_key = plural_map.get(kind, kind + "s")
            bag = entities.as_dict()
            if not bag.get(bag_key):
                logger.info("[tier0] required entity '%s' not in query", kind)
                return None

    # Step 6 — assemble tool arguments. Support BOTH:
    #   • param_map  : single dict — one attempt
    #   • param_maps : list of dicts — try in order, stop on first hit
    param_maps_raw = t0_cfg.get("param_maps")
    if isinstance(param_maps_raw, list) and param_maps_raw:
        attempts = param_maps_raw
    else:
        attempts = [t0_cfg.get("param_map") or {}]

    template = t0_cfg.get("template") or ""
    required_fields = t0_cfg.get("required_fields") or []
    rendered: str | None = None
    hit_arguments: dict | None = None
    hit_output: str | None = None

    # Extra entity bag — carries keyword_extract if we resolved one
    extra_entities: dict[str, str] | None = (
        {"keyword_extract": keyword_extracted} if keyword_extracted else None
    )

    for attempt_idx, pmap in enumerate(attempts):
        if not isinstance(pmap, dict):
            continue
        arguments: dict = {}
        skip = False
        for path, ref in pmap.items():
            val = (
                _entity_value(entities, ref, extra=extra_entities)
                if isinstance(ref, str) and ref.startswith("$")
                else ref
            )
            if val is None:
                logger.info(
                    "[tier0] attempt %d: param %r is None — skipping attempt",
                    attempt_idx, path,
                )
                skip = True
                break
            _set_at_path(arguments, path, val)
        if skip:
            continue

        # Step 7 — execute the tool. Forward the request _context (actor,
        # redact_fields, ids) so PII redaction and actor forced-filters apply
        # on the Tier 0 path exactly as on the LLM path.
        _t0_cfg = top_tool.config_json
        if tool_context is not None and isinstance(_t0_cfg, dict):
            _t0_cfg = {**_t0_cfg, "_context": tool_context}
        try:
            result = await execute_tool(top_tool.name, arguments, _t0_cfg)
        except Exception:
            logger.exception("[tier0] attempt %d execution failed", attempt_idx)
            continue
        if not result.success or not result.output:
            logger.info("[tier0] attempt %d: tool returned failure", attempt_idx)
            continue

        # Step 8 — render template.
        # `raw_output: true` in tier0_template skips JSON parsing and returns
        # the tool output as-is (useful for plain-text tools like ping/traceroute).
        not_found_hit = False
        if t0_cfg.get("raw_output"):
            rendered = result.output.strip() or None
        else:
            try:
                data = json.loads(result.output)
            except (ValueError, TypeError):
                logger.info("[tier0] attempt %d: non-JSON output", attempt_idx)
                continue
            # No matching record? Use not_found_template (if configured) instead
            # of either emitting half-rendered {MISSING} junk or bailing to the LLM.
            not_found_tpl = t0_cfg.get("not_found_template")
            if _result_is_empty(data, required_fields):
                not_found_hit = True
                if t0_cfg.get("not_found_fallthrough"):
                    # Hand the miss to the LLM so it can clarify, suggest a
                    # variant, or continue the dialog (agent-style) instead of
                    # dead-ending on a static "не найдено". The static
                    # not_found_template (if any) is ignored when this is set.
                    rendered = None
                elif not_found_tpl:
                    rendered = _render_not_found(not_found_tpl, entities, keyword_extracted, user_query)
                else:
                    rendered = None
            else:
                # Data IS present — render the normal template. If it fails here
                # the template paths are wrong (a config error), NOT a "not found":
                # fall through to the LLM so the misconfig surfaces instead of a
                # misleading "не найдено" on a record that actually exists.
                rendered = _render_template(template, data, required_fields,
                                            table_defs=t0_cfg.get("table_defs"),
                                            value_maps=t0_cfg.get("value_maps"))
        if rendered is not None:
            hit_arguments = arguments
            hit_output = None if not_found_hit else result.output
            if attempt_idx > 0:
                logger.info(
                    "[tier0] succeeded on attempt %d (after %d misses)",
                    attempt_idx, attempt_idx,
                )
            break

    if rendered is None:
        return None

    latency_ms = (time.perf_counter() - t_start) * 1000
    logger.info(
        "[tier0] ✅ %s in %.0fms (score=%.3f gap=%.3f)",
        top_tool.name, latency_ms, top_score, top_score - second_score,
    )

    ent_dict = entities.as_dict()
    if keyword_extracted:
        ent_dict["keyword_extract"] = [keyword_extracted]

    return Tier0Result(
        content=rendered,
        tool_name=top_tool.name,
        confidence=top_score,
        second_score=second_score,
        latency_ms=latency_ms,
        extracted_entities=ent_dict,
        arguments=hit_arguments,
        tool_output=hit_output,
    )


# ---------------------------------------------------------------------------
# Diagnostics — read-only "explain" of the Tier 0 decision for one query.
# Mirrors the gate logic above but accumulates a structured trace instead of
# just logging, and scans ALL tier0 tools (not only top-K) so competing /
# duplicate regex matches are visible. Safe to call from admin UI.
# ---------------------------------------------------------------------------

def _regex_match(kw_regex: str, query: str, strip_prefixes: list[str] | None) -> tuple[bool, str | None, str | None]:
    """Return (matched, extracted, error). Mirrors runtime: re.match + strip."""
    if not kw_regex:
        return False, None, "keyword_regex пуст"
    try:
        m = re.match(kw_regex, query, re.IGNORECASE)
    except re.error as exc:
        return False, None, f"ошибка regex: {exc}"
    if not (m and m.lastindex and m.lastindex >= 1):
        return False, None, None
    extracted = m.group(1).strip()
    for sp in (strip_prefixes or []):
        if sp and extracted.lower().startswith(sp.lower()):
            extracted = extracted[len(sp):].strip()
            break
    if not extracted:
        return False, None, "пустой захват после strip_prefixes"
    return True, extracted, None


async def explain_tier0(
    *,
    tenant_id: str,
    user_query: str,
    db: AsyncSession,
    embedding_model: str | None = None,
    min_tool_score: float = 0.80,
    max_score_gap: float = 0.15,
    focus_tool: str | None = None,
    run_tool: bool = False,
    override_tier0: dict | None = None,
) -> dict:
    """Explain why Tier 0 would or would not fire for `user_query`, with a full
    trace, competing-tool matches, and recommendations (esp. for `focus_tool`).

    If `override_tier0` is given, it replaces `focus_tool`'s stored tier0_template
    for this run — so the admin can test an unsaved config straight from the editor
    or wizard, before persisting it.
    """
    from app.models.tenant_tool import TenantTool
    from sqlalchemy import select as _select

    def _t0_of(tool):
        """tier0 config for a tool, honouring the unsaved override on focus_tool."""
        if override_tier0 is not None and focus_tool and tool.name == focus_tool:
            t0 = override_tier0
            if not isinstance(t0, dict):
                return None
            if not t0.get("raw_output") and not t0.get("template"):
                return None
            return t0
        return _extract_tier0_config(tool)

    user_query = (user_query or "").strip()
    steps: list[dict] = []
    recs: list[dict] = []

    entities = extract_entities(user_query)
    ent_dict = entities.as_dict()

    # ── Semantic ranking (a bit wider than runtime for visibility) ──────────
    ranking: list[dict] = []
    topk_ids: set = set()
    try:
        top = await search_tools(
            tenant_id=tenant_id, query=user_query, db=db,
            embedding_model=embedding_model, top_k=8,
        )
    except Exception as exc:
        top = []
        steps.append({"label": "Семантический поиск", "status": "fail",
                      "detail": f"ошибка: {exc}"})
    for rank, t in enumerate(top):
        raw = float(getattr(t, "_semantic_score", 0.0) or 0.0)
        bonus, matched = _compute_entity_boost(t, entities)
        t0 = _t0_of(t)
        topk_ids.add(t.id)
        ranking.append({
            "name": t.name,
            "raw_score": round(raw, 3),
            "entity_boost": round(bonus, 2),
            "total_score": round(raw + bonus, 3),
            "rank": rank,
            "has_tier0": t0 is not None,
            "required_entity": (t0 or {}).get("required_entity"),
            "matched_entities": matched,
        })

    # ── Competing regex matches across ALL tier0 keyword_extract tools ──────
    all_tools = (await db.execute(
        _select(TenantTool).where(TenantTool.tenant_id == uuid_or(tenant_id))
    )).scalars().all()
    score_by_id = {t.id: float(getattr(t, "_semantic_score", 0.0) or 0.0) for t in top}
    rank_by_id = {t.id: i for i, t in enumerate(top)}
    regex_matches: list[dict] = []
    for t in all_tools:
        t0 = _t0_of(t)
        if not t0 or t0.get("required_entity") != "keyword_extract":
            continue
        blocked = _block_hit(t0.get("block_keywords"), user_query)
        matched, extracted, err = _regex_match(
            t0.get("keyword_regex") or "", user_query, t0.get("strip_prefixes"))
        if matched:
            regex_matches.append({
                "name": t.name,
                "extracted": extracted,
                "in_topk": t.id in topk_ids,
                "rank": rank_by_id.get(t.id),
                "score": round(score_by_id.get(t.id, 0.0), 3) if t.id in score_by_id else None,
                "blocked_by": blocked,
            })
    # Order competitors by semantic score desc (None last)
    regex_matches.sort(key=lambda r: (r["score"] is None, -(r["score"] or 0.0)))

    # ── Replicate the decision ──────────────────────────────────────────────
    decision: dict = {"fired": False, "tool": None, "path": "none",
                      "reason": "", "extracted_keyword": None,
                      "arguments": None, "tool_output": None, "rendered": None}

    eligible = [t for t in top if _t0_of(t) is not None]
    if not top:
        decision["reason"] = "семантический поиск не вернул инструментов"
        steps.append({"label": "Кандидаты", "status": "fail", "detail": "top-K пуст"})
    elif not eligible:
        decision["reason"] = "ни один из top-K инструментов не имеет tier0_template"
        steps.append({"label": "Кандидаты Tier 0", "status": "fail",
                      "detail": "в top-K нет инструментов с настроенным Tier 0"})
    else:
        steps.append({"label": "Кандидаты Tier 0", "status": "ok",
                      "detail": f"{len(eligible)} из {len(top)} инструментов с tier0_template"})

    REGEX_SANITY_FLOOR = 0.25
    winner = None
    win_t0 = None
    win_kw = None
    win_path = None

    # Tier A — regex-first over eligible keyword_extract tools (score order)
    boosted = []
    for t in eligible:
        raw = float(getattr(t, "_semantic_score", 0.0) or 0.0)
        bonus, _ = _compute_entity_boost(t, entities)
        boosted.append((t, raw + bonus))
    boosted.sort(key=lambda x: -x[1])

    for t, score in boosted:
        t0 = _t0_of(t)
        if t0.get("required_entity") != "keyword_extract":
            continue
        if score < REGEX_SANITY_FLOOR:
            continue
        blocked = _block_hit(t0.get("block_keywords"), user_query)
        if blocked:
            continue
        matched, extracted, err = _regex_match(
            t0.get("keyword_regex") or "", user_query, t0.get("strip_prefixes"))
        if matched:
            winner, win_t0, win_kw, win_path = t, t0, extracted, "regex-first"
            break

    # Tier B — semantic gate (only if no regex winner)
    if winner is None and eligible:
        boosted_full = []
        for t in top:
            raw = float(getattr(t, "_semantic_score", 0.0) or 0.0)
            bonus, _ = _compute_entity_boost(t, entities)
            boosted_full.append((t, raw + bonus))
        boosted_full.sort(key=lambda x: -x[1])
        elig_sorted = [(t, s) for t, s in boosted_full if _t0_of(t) is not None]
        if elig_sorted:
            top_tool, top_score = elig_sorted[0]
            competitors = [s for t, s in boosted_full if t.id != top_tool.id]
            second = competitors[0] if competitors else 0.0
            if top_score < min_tool_score:
                steps.append({"label": "Семантический гейт", "status": "fail",
                              "detail": f"score топа {top_score:.3f} < порога {min_tool_score:.2f}"})
                decision["reason"] = (f"лучший Tier 0 инструмент «{top_tool.name}» имеет score "
                                      f"{top_score:.3f}, что ниже порога {min_tool_score:.2f}")
            elif (top_score - second) < max_score_gap:
                steps.append({"label": "Семантический гейт", "status": "fail",
                              "detail": f"разрыв до 2-го мал: {top_score:.3f}−{second:.3f} < {max_score_gap:.2f}"})
                decision["reason"] = (f"«{top_tool.name}» недостаточно оторвался от конкурента "
                                      f"(разрыв {top_score - second:.3f} < {max_score_gap:.2f})")
            else:
                winner, win_t0, win_path = top_tool, _t0_of(top_tool), "semantic-gate"

    if winner is not None:
        decision["tool"] = winner.name
        decision["path"] = win_path
        decision["extracted_keyword"] = win_kw
        steps.append({"label": "Выбран инструмент", "status": "ok",
                      "detail": f"«{winner.name}» через {win_path}"
                                + (f", извлечено: {win_kw!r}" if win_kw else "")})

        # Step — assemble arguments (this is where many configs silently die)
        attempts = win_t0.get("param_maps") if isinstance(win_t0.get("param_maps"), list) and win_t0.get("param_maps") \
            else [win_t0.get("param_map") or {}]
        extra = {"keyword_extract": win_kw} if win_kw else None
        assembled = None
        arg_fail = None
        for ai, pmap in enumerate(attempts):
            if not isinstance(pmap, dict):
                continue
            args: dict = {}
            bad = None
            for path, ref in pmap.items():
                val = (_entity_value(entities, ref, extra=extra)
                       if isinstance(ref, str) and ref.startswith("$") else ref)
                if val is None:
                    bad = (path, ref)
                    break
                _set_at_path(args, path, val)
            if bad is None:
                assembled = args
                break
            arg_fail = bad
        if not any(isinstance(p, dict) and p for p in attempts):
            steps.append({"label": "Сборка аргументов", "status": "fail",
                          "detail": "param_maps не заданы — инструмент будет вызван без аргументов"})
            decision["reason"] = "не настроен param_maps: нечего передать инструменту"
            recs.append({"severity": "error",
                         "text": _suggest_param_map(winner, win_t0)})
        elif assembled is None and arg_fail is not None:
            path, ref = arg_fail
            steps.append({"label": "Сборка аргументов", "status": "fail",
                          "detail": f"параметр {path} ← {ref} не разрешился (нет такой сущности в запросе)"})
            decision["reason"] = (f"param_maps ссылается на {ref}, но в запросе нет этой сущности — "
                                  f"аргумент {path} не собран")
            if win_t0.get("required_entity") == "keyword_extract" and isinstance(ref, str) and "keyword_extract" not in ref:
                recs.append({"severity": "error",
                             "text": _suggest_param_map(winner, win_t0,
                                     note=f"сейчас стоит {path} ← {ref}, а сущность инструмента — keyword_extract")})
        else:
            decision["arguments"] = assembled
            steps.append({"label": "Сборка аргументов", "status": "ok",
                          "detail": json.dumps(assembled, ensure_ascii=False)})
            if run_tool:
                try:
                    result = await execute_tool(winner.name, assembled, winner.config_json)
                except Exception as exc:
                    steps.append({"label": "Вызов инструмента", "status": "fail", "detail": f"исключение: {exc}"})
                    decision["reason"] = f"инструмент упал при вызове: {exc}"
                    result = None
                if result is not None:
                    if not result.success or not result.output:
                        _err = (getattr(result, "error", None) or "").strip()
                        if _err:
                            _detail = f"ошибка инструмента: {_err}"
                        elif result.success:
                            _detail = "инструмент вернул пустой ответ (0 записей)"
                        else:
                            _detail = "инструмент вернул неуспех без текста ошибки"
                        steps.append({"label": "Вызов инструмента", "status": "fail",
                                      "detail": _detail[:1500]})
                        decision["reason"] = _detail[:800]
                        # Some tools put error text in output, not error — show it too.
                        if result.output:
                            decision["tool_output"] = result.output[:4000]
                    else:
                        decision["tool_output"] = (result.output or "")[:4000]
                        steps.append({"label": "Вызов инструмента", "status": "ok",
                                      "detail": "успех"})
                        not_found_tpl = win_t0.get("not_found_template")
                        is_empty = False
                        if win_t0.get("raw_output"):
                            rendered = (result.output or "").strip() or None
                        else:
                            try:
                                data = json.loads(result.output)
                            except (ValueError, TypeError):
                                data = None
                                rendered = None
                                steps.append({"label": "Шаблон", "status": "fail",
                                              "detail": "вывод инструмента — не JSON"})
                            else:
                                is_empty = _result_is_empty(data, win_t0.get("required_fields") or [])
                                if is_empty:
                                    rendered = (
                                        _render_not_found(not_found_tpl, entities, win_kw, user_query)
                                        if not_found_tpl else None
                                    )
                                else:
                                    # Data present but template failed → wrong
                                    # paths (config error), not a "not found".
                                    rendered = _render_template(
                                        win_t0.get("template") or "", data,
                                        win_t0.get("required_fields") or [],
                                        table_defs=win_t0.get("table_defs"),
                                        value_maps=win_t0.get("value_maps"))
                        if rendered is None:
                            if is_empty and not not_found_tpl:
                                steps.append({"label": "Результат", "status": "fail",
                                              "detail": "инструмент вернул пусто (запись не найдена)"})
                                decision["reason"] = "запись не найдена, и не задан not_found_template — уход в LLM"
                                recs.append({"severity": "info",
                                             "text": "Добавьте «Шаблон: не найдено», чтобы Tier 0 отвечал, например, "
                                                     "«… не найдено», вместо ухода в LLM на пустом результате."})
                            else:
                                steps.append({"label": "Шаблон", "status": "fail",
                                              "detail": "не отрендерился (проверьте поля шаблона / required_fields)"})
                                decision["reason"] = ("данные есть, но шаблон не сошёлся с выводом — "
                                                      "запись существует, это ошибка путей в шаблоне")
                                # Smart hint: output wraps rows in an array, template uses bare names.
                                container = None
                                if isinstance(data, dict):
                                    container = next((k for k in ("items", "results", "rows", "records", "data", "list")
                                                      if isinstance(data.get(k), list) and data.get(k)), None)
                                tmpl = win_t0.get("template") or ""
                                if container and container + "." not in tmpl:
                                    sample_keys = list((data[container][0] or {}).keys())[:4] if isinstance(data[container][0], dict) else []
                                    recs.append({"severity": "error",
                                                 "text": f"Запись НАЙДЕНА, но шаблон ссылается на поля без префикса массива. "
                                                         f"Вывод оборачивает строки в «{container}» — используйте пути вида "
                                                         f"`{{{container}.0.<поле>}}` (например {{{container}.0.{(sample_keys or ['name'])[0]}}}). "
                                                         f"Доступные поля: {', '.join(sample_keys) or '—'}."})
                                else:
                                    recs.append({"severity": "warning",
                                                 "text": "Поля шаблона/required_fields не совпадают с реальным выводом "
                                                         "инструмента — сверьте пути с блоком «JSON-вывод инструмента»."})
                        else:
                            decision["rendered"] = rendered
                            decision["fired"] = True
                            decision["reason"] = ("запись не найдена → ответ по not_found_template"
                                                  if is_empty else "Tier 0 сработал бы и отрендерил ответ")
                            steps.append({"label": "Шаблон", "status": "ok",
                                          "detail": "ответ «не найдено»" if is_empty else "отрендерился"})
            else:
                # Not running the tool — args assembled is as far as we go.
                decision["fired"] = True
                decision["reason"] = "матчинг и сборка аргументов прошли (инструмент не вызывался)"

    # ── Recommendations: competitors & focus tool ───────────────────────────
    if len(regex_matches) > 1:
        others = [r["name"] for r in regex_matches if r["name"] != (decision["tool"] or "")]
        if others:
            recs.append({"severity": "warning",
                         "text": "Этот запрос ловят несколько инструментов: "
                                 + ", ".join(f"«{n}»" for n in [decision["tool"]] + others if n)
                                 + ". Победит инструмент с наибольшим семантическим score; "
                                   "сузьте regex или добавьте block_keywords у лишних, чтобы избежать конфликтов."})

    if focus_tool and decision.get("tool") != focus_tool:
        ft = next((t for t in all_tools if t.name == focus_tool), None)
        if ft is not None:
            ft0 = _t0_of(ft)
            if ft0 is None:
                recs.append({"severity": "info",
                             "text": f"«{focus_tool}» не имеет валидного tier0_template (нет template?) — "
                                     "поэтому он не участвует в Tier 0."})
            else:
                in_top = ft.id in topk_ids
                if not in_top:
                    recs.append({"severity": "warning",
                                 "text": f"«{focus_tool}» не попал в top-8 семантического поиска для этого запроса. "
                                         "Tier 0 рассматривает только top-K. Уточните описание/примеры инструмента, "
                                         "чтобы поднять его релевантность."})
                else:
                    matched, extracted, err = _regex_match(
                        ft0.get("keyword_regex") or "", user_query, ft0.get("strip_prefixes"))
                    blocked = _block_hit(ft0.get("block_keywords"), user_query)
                    if ft0.get("required_entity") == "keyword_extract" and not matched:
                        recs.append({"severity": "warning",
                                     "text": f"regex «{focus_tool}» не совпал с этим запросом"
                                             + (f" ({err})" if err else "") + " — доработайте паттерн через визард."})
                    elif blocked:
                        recs.append({"severity": "info",
                                     "text": f"«{focus_tool}» заблокирован своим block_keyword «{blocked}» для этого запроса."})
                    elif decision.get("tool"):
                        recs.append({"severity": "info",
                                     "text": f"«{focus_tool}» совпал, но выиграл «{decision['tool']}» "
                                             "(выше семантический score). Разведите их regex/описания."})

    return {
        "tenant_tier0_enabled": True,  # caller checks; informational
        "min_tool_score": min_tool_score,
        "max_score_gap": max_score_gap,
        "query": user_query,
        "entities": ent_dict,
        "ranking": ranking,
        "regex_matches": regex_matches,
        "decision": decision,
        "steps": steps,
        "recommendations": recs,
        "focus_tool": focus_tool,
    }


def _suggest_param_map(tool, t0: dict, note: str | None = None) -> str:
    """Heuristic: suggest a param_maps entry mapping the entity to a likely tool arg."""
    cfg = getattr(tool, "config_json", None) or {}
    fn = cfg.get("function") or {}
    props = ((fn.get("parameters") or {}).get("properties") or {})
    names = list(props.keys())
    ent = t0.get("required_entity")
    ref = f"${ent}" if ent else "$keyword_extract"
    # Prefer an obvious text/search arg name.
    pref = next((n for n in names if n.lower() in
                 ("query", "q", "search", "keyword", "name", "text", "value")), None)
    target = pref or (names[0] if names else "query")
    base = (f"Добавьте param_maps: {{\"{target}\": \"{ref}\"}} "
            f"(доступные параметры инструмента: {', '.join(names) or '—'}).")
    return (note + ". " + base) if note else base


def uuid_or(v):
    """Accept str or UUID for tenant_id filters."""
    import uuid as _uuid
    return v if isinstance(v, _uuid.UUID) else _uuid.UUID(str(v))
