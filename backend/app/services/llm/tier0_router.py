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

    def __init__(self, table_defs: dict | None = None):
        super().__init__()
        # table_defs: {field_name: {"columns": [{"field":…,"label":…,"values":…,…}]}}
        self._table_defs: dict = table_defs or {}
        self._current_field: str = ""  # set by get_field before format_field

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
            return "{MISSING}"

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
        return _SafeFormatter(table_defs=table_defs).format(template, _root=data)
    except Exception:
        logger.exception("[tier0] template render failed")
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
) -> Tier0Result | None:
    """Run the Tier 0 router. Returns None to signal "fall back to LLM"."""
    if not (user_query and user_query.strip()):
        return None
    if not embedding_model:
        return None  # need embeddings to do semantic match

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
        uq_lower = user_query.lower()
        if any(bk and bk.lower() in uq_lower for bk in block_keywords):
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

        # Step 7 — execute the tool
        try:
            result = await execute_tool(top_tool.name, arguments, top_tool.config_json)
        except Exception:
            logger.exception("[tier0] attempt %d execution failed", attempt_idx)
            continue
        if not result.success or not result.output:
            logger.info("[tier0] attempt %d: tool returned failure", attempt_idx)
            continue

        # Step 8 — render template.
        # `raw_output: true` in tier0_template skips JSON parsing and returns
        # the tool output as-is (useful for plain-text tools like ping/traceroute).
        if t0_cfg.get("raw_output"):
            rendered = result.output.strip() or None
        else:
            try:
                data = json.loads(result.output)
            except (ValueError, TypeError):
                logger.info("[tier0] attempt %d: non-JSON output", attempt_idx)
                continue
            rendered = _render_template(template, data, required_fields,
                                        table_defs=t0_cfg.get("table_defs"))
        if rendered is not None:
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
    )
