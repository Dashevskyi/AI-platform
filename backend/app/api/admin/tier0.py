"""Admin endpoints for Tier 0 routing observability.

Reads tier0 metadata from messages.metadata_json (saved by chats API after
each turn — see admin/chats.py and tenant/chats.py).
"""
import json
import logging
import re
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import decrypt_value
from app.models.tenant_shell_config import TenantShellConfig
from app.api.deps import require_role, require_tenant_access, require_permission
from app.providers.factory import get_provider

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin/tenants/{tenant_id}/tier0",
    tags=["admin-tier0"],
    dependencies=[
        Depends(require_role("superadmin", "tenant_admin")),
        Depends(require_tenant_access),
        Depends(require_permission("logs")),
    ],
)


@router.get("/stats")
async def get_tier0_stats(
    tenant_id: uuid.UUID,
    days: int = Query(7, ge=1, le=90),
    recent_limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Hit rate, by-tool breakdown, recent hits — all derived from
    messages.metadata_json (the `tier0` key set on assistant messages that
    bypassed the LLM)."""
    config = (
        await db.execute(
            select(TenantShellConfig).where(TenantShellConfig.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Total assistant messages in window (denominator for hit-rate).
    total_q = await db.execute(
        text(
            "SELECT COUNT(*) FROM messages "
            "WHERE tenant_id = :tid AND role = 'assistant' AND created_at >= :cutoff"
        ),
        {"tid": str(tenant_id), "cutoff": cutoff},
    )
    total = int(total_q.scalar() or 0)

    # Tier-0 hit messages.
    hits_q = await db.execute(
        text(
            "SELECT id, created_at, chat_id, "
            "       metadata_json->'tier0'->>'tool' AS tool, "
            "       (metadata_json->'tier0'->>'confidence')::float AS confidence, "
            "       latency_ms, "
            "       metadata_json->'tier0'->'entities' AS entities, "
            "       content AS rendered_output "
            "FROM messages "
            "WHERE tenant_id = :tid AND role = 'assistant' "
            "  AND created_at >= :cutoff "
            "  AND metadata_json ? 'tier0' "
            "  AND metadata_json->'tier0' IS NOT NULL "
            "  AND jsonb_typeof(metadata_json->'tier0') = 'object' "
            "ORDER BY created_at DESC"
        ),
        {"tid": str(tenant_id), "cutoff": cutoff},
    )
    hit_rows = hits_q.all()

    # Pull the user query that preceded each hit (for the recent_hits list).
    # Done one query at a time — fine for recent_limit=20; if we ever need
    # bigger windows, switch to a single window-function query.
    recent_hits = []
    for row in hit_rows[:recent_limit]:
        try:
            user_q = await db.execute(
                text(
                    "SELECT content FROM messages "
                    "WHERE chat_id = :cid AND tenant_id = :tid "
                    "  AND role = 'user' AND created_at < :ts "
                    "ORDER BY created_at DESC LIMIT 1"
                ),
                {"cid": str(row.chat_id) if row.chat_id else None, "tid": str(tenant_id), "ts": row.created_at},
            )
            user_content = user_q.scalar() or ""
            recent_hits.append({
                "message_id": str(row.id),
                "chat_id": str(row.chat_id) if row.chat_id else None,
                "ts": row.created_at.isoformat() if row.created_at else None,
                "tool": row.tool,
                "confidence": row.confidence,
                "latency_ms": row.latency_ms,
                "user_query": user_content[:200],
                "entities": row.entities,
                "rendered_output": (row.rendered_output or "")[:1000],
            })
        except Exception:
            continue

    # Aggregate by tool.
    by_tool: dict[str, dict] = {}
    for row in hit_rows:
        t = row.tool or "(unknown)"
        agg = by_tool.setdefault(t, {"tool": t, "count": 0, "sum_ms": 0.0, "n_ms": 0})
        agg["count"] += 1
        if row.latency_ms is not None:
            agg["sum_ms"] += float(row.latency_ms)
            agg["n_ms"] += 1
    by_tool_list = sorted(
        [
            {
                "tool": v["tool"],
                "count": v["count"],
                "avg_ms": round(v["sum_ms"] / v["n_ms"], 1) if v["n_ms"] else None,
            }
            for v in by_tool.values()
        ],
        key=lambda r: -r["count"],
    )

    total_hits = len(hit_rows)
    avg_ms_all = None
    ms_vals = [float(r.latency_ms) for r in hit_rows if r.latency_ms is not None]
    if ms_vals:
        avg_ms_all = round(sum(ms_vals) / len(ms_vals), 1)

    return {
        "enabled": bool(getattr(config, "tier0_enabled", False)) if config else False,
        "min_tool_score": float(getattr(config, "tier0_min_tool_score", 0.80)) if config else 0.80,
        "max_score_gap": float(getattr(config, "tier0_max_score_gap", 0.15)) if config else 0.15,
        "lookback_days": days,
        "total_assistant_messages": total,
        "tier0_hits": total_hits,
        "hit_rate_pct": round(100.0 * total_hits / total, 1) if total else 0.0,
        "avg_latency_ms": avg_ms_all,
        "by_tool": by_tool_list,
        "recent_hits": recent_hits,
    }


@router.get("/audit")
async def get_tier0_audit(
    tenant_id: uuid.UUID,
    days: int = Query(30, ge=1, le=90),
    min_calls: int = Query(3, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Analyse recent LLM logs to find tools that are called frequently via LLM
    but have no Tier 0 template configured. Returns ranked candidates with
    sample user queries so the admin can set up Tier 0 for them.

    Data source: llm_request_logs.debug->'tool_calls'  (written by pipeline.py
    for every tool call). Filters to single-tool-call rounds (simple queries)
    that succeeded.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # 1. Pull single-tool-call LLM rounds with user_query from the debug field.
    #    tool_calls_count = 1 → only simple "one question → one tool" patterns.
    rows = (await db.execute(
        text("""
            SELECT
                debug->>'user_query'                       AS user_query,
                debug->'tool_calls'->0->>'name'            AS tool_name,
                debug->'tool_calls'->0->>'args_preview'    AS args_preview,
                (debug->'tool_calls'->0->>'ok')::boolean   AS tool_ok
            FROM llm_request_logs
            WHERE tenant_id       = :tid
              AND created_at      >= :cutoff
              AND tool_calls_count = 1
              AND status          = 'success'
              AND debug           IS NOT NULL
              AND jsonb_typeof(debug->'tool_calls') = 'array'
              AND jsonb_array_length(debug->'tool_calls') = 1
            ORDER BY created_at DESC
            LIMIT 5000
        """),
        {"tid": str(tenant_id), "cutoff": cutoff},
    )).fetchall()

    # 2. Group by tool_name; keep successful calls only.
    tool_queries: dict[str, list[str]] = defaultdict(list)
    tool_args: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        if not row.tool_name or not row.tool_ok:
            continue
        if row.user_query:
            tool_queries[row.tool_name].append(row.user_query.strip())
        if row.args_preview:
            tool_args[row.tool_name].append(row.args_preview)

    # 3. Fetch tier0 config status for all tenant tools.
    tools_rows = (await db.execute(
        text("""
            SELECT
                name,
                config_json->'x_backend_config'->'tier0_template' IS NOT NULL
                    AND config_json->'x_backend_config'->'tier0_template' != 'null'::jsonb
                    AS has_tier0,
                config_json->'x_backend_config'->'tier0_template'->>'required_entity'
                    AS entity
            FROM tenant_tools
            WHERE tenant_id = :tid
        """),
        {"tid": str(tenant_id)},
    )).fetchall()
    tier0_map = {r.name: bool(r.has_tier0) for r in tools_rows}

    # 4. Build result candidates.
    candidates = []
    for tool_name, queries in tool_queries.items():
        count = len(queries)
        if count < min_calls:
            continue
        has_tier0 = tier0_map.get(tool_name, False)

        # Deduplicate (case-insensitive) while preserving original casing.
        seen: set[str] = set()
        unique_queries: list[str] = []
        for q in queries:  # already DESC order from query
            key = q.lower()
            if key not in seen:
                seen.add(key)
                unique_queries.append(q)

        # Sample args to help admin understand what params the tool uses.
        seen_args: set[str] = set()
        unique_args: list[str] = []
        for a in tool_args.get(tool_name, []):
            if a not in seen_args:
                seen_args.add(a)
                unique_args.append(a)

        # Simple recommendation based on frequency gap.
        if not has_tier0:
            if count >= 20:
                priority = "high"
            elif count >= 7:
                priority = "medium"
            else:
                priority = "low"
        else:
            priority = "configured"

        candidates.append({
            "tool_name": tool_name,
            "call_count": count,
            "unique_query_count": len(seen),
            "has_tier0": has_tier0,
            "priority": priority,
            "sample_queries": unique_queries[:8],
            "sample_args": unique_args[:4],
        })

    # Sort: not-configured first (by priority then count), then configured.
    priority_order = {"high": 0, "medium": 1, "low": 2, "configured": 3}
    candidates.sort(key=lambda x: (priority_order.get(x["priority"], 9), -x["call_count"]))

    return {
        "candidates": candidates,
        "period_days": days,
        "min_calls": min_calls,
        "total_rows_analyzed": len(rows),
    }


# ---------------------------------------------------------------------------
# Tier 0 AI assistant
# ---------------------------------------------------------------------------

class Tier0AssistRequest(BaseModel):
    user_message: str
    tool_name: str
    tool_description: str
    current_tier0: dict | None = None


def _build_tier0_assist_prompt(
    tool_name: str,
    tool_description: str,
    current_tier0: dict | None,
) -> str:
    current_tier0_str = (
        json.dumps(current_tier0, ensure_ascii=False, indent=2)
        if current_tier0 is not None
        else "null (not configured yet)"
    )
    return f"""You are an expert assistant that helps configure Tier 0 deterministic routing for an AI platform.

## What is Tier 0?

Tier 0 is a deterministic routing shortcut that bypasses the LLM entirely for simple, repetitive queries. When a user message matches a pattern, the platform extracts an entity (e.g. a street name, phone number, account number), calls the tool directly, and renders the result using a markdown template — all without any LLM call. This dramatically reduces latency and cost for high-frequency simple queries.

## Configurable fields

### `required_entity`
Controls what entity is extracted from the user query before calling the tool.

- `"keyword_extract"` — captures arbitrary text using `keyword_regex`. The captured group becomes `$keyword_extract` and is passed as the tool argument.
- `"phone"` — extracts a phone number from the query.
- `"account_number"` — extracts an account/contract number from the query.
- `null` — no entity extraction; the tool is called with no arguments (e.g. "show me my balance").

### `keyword_regex`
A Python regex string with **exactly ONE capture group**. The text matched by the capture group becomes `$keyword_extract`.

Rules for writing the regex:
- The capture group `(...)` must contain the value you want to extract.
- Anchors are not required; the regex is searched anywhere in the message (case-insensitive).
- For simple keyword patterns (where the value follows a preposition directly), write: `(?:по|на|для|вулиці?)\\s+([\\w\\s\\-\\.]+?)(?:\\s+\\d|$|[?!.,;])`
- For qualifier patterns where a noun comes between the preposition and value (e.g. "по вулиці Мелешкіна", "по адресу X"), include the noun inside the regex: `(?:по вулиці|по адресу|на вулиці)\\s+([\\w\\s\\-\\.]+?)(?:\\s+\\d|$|[?!.,;])`
- Always make the capture group non-greedy when followed by optional digits or sentence-ending tokens.

### `strip_prefixes`
A list of strings that are stripped from the **start** of the captured keyword (case-insensitive).

Use `strip_prefixes` ONLY when a preposition or context word appears **directly** before the value without an intervening noun. Examples:
- "на Мелешкіна" → `strip_prefixes: ["на "]` (preposition directly before street name)
- "по Косарева" → `strip_prefixes: ["по "]`

Do NOT use `strip_prefixes` if the noun is part of the pattern (e.g. "по вулиці X" — the regex already handles "по вулиці", so no stripping needed).

### `block_keywords`
A list of substrings. If **any** of these appears in the user query, Tier 0 is skipped and the query is sent to the LLM instead.

Use `block_keywords` for queries that match the Tier 0 pattern on the surface but have extra conditions or complexity the tool cannot handle deterministically. Examples:
- `"з тарифом"` — "покажи абонента Іванова з тарифом Домашній" looks like a name lookup but has a filter
- `"за останній місяць"` — adds a time range condition
- `"і"`, `"та"` — conjunctions suggesting multiple entities
- `"всіх"`, `"список"` — list queries, not single-entity lookups

### `template`
A Markdown string used to render the tool result. Use `{{field.path}}` syntax to reference fields from the tool's JSON response.

Examples:
- `"{{subscriber.name}}"` — subscriber's name
- `"{{address.street}}, {{address.building}}"` — formatted address
- `"Баланс: **{{balance.amount}} грн**"` — bold balance

## Examples

### Example 1: Street lookup (qualifier word pattern)

User wants: "look up by street name, queries like 'покажи по вулиці Шевченка'"

```json
{{
  "required_entity": "keyword_extract",
  "keyword_regex": "(?:по вулиці|на вулиці|вулиця)\\\\s+([\\\\w\\\\s\\\\-\\\\.]+?)(?:\\\\s+\\\\d|$|[?!.,;])",
  "strip_prefixes": [],
  "block_keywords": ["з тарифом", "за останній місяць", "і будинку", "список"],
  "template": "## Абоненти на вулиці {{street}}\\n{{#subscribers}}\\n- **{{name}}** — {{account_number}}\\n{{/subscribers}}"
}}
```
Note: no `strip_prefixes` because the regex already consumes "по вулиці".

### Example 2: Simple preposition pattern (no qualifier noun)

User wants: "look up by street name, queries like 'на Мелешкіна', 'по Косарева'"

```json
{{
  "required_entity": "keyword_extract",
  "keyword_regex": "(?:на|по|для)\\\\s+([\\\\w\\\\s\\\\-\\\\.]+?)(?:\\\\s+\\\\d|$|[?!.,;])",
  "strip_prefixes": ["на ", "по ", "для "],
  "block_keywords": ["з тарифом", "і будинку"],
  "template": "## Результат пошуку по {{keyword}}\\n{{result}}"
}}
```
Note: `strip_prefixes` cleans prepositions that the regex didn't consume as qualifier words.

### Example 3: Phone lookup (built-in entity)

```json
{{
  "required_entity": "phone",
  "keyword_regex": null,
  "strip_prefixes": [],
  "block_keywords": [],
  "template": "**{{subscriber.name}}**\\nТариф: {{tariff.name}}\\nБаланс: {{balance}} грн"
}}
```

## Tool context

**Tool name:** {tool_name}
**Tool description:** {tool_description}

**Current tier0_template:**
```json
{current_tier0_str}
```

## Your task

Based on the admin's request below, produce or update the `tier0_template` configuration for this tool.

Respond ONLY with a JSON object (no markdown wrapper, no extra text) in exactly this format:
{{
  "suggestion": {{
    "required_entity": "...",
    "keyword_regex": "...",
    "strip_prefixes": [...],
    "block_keywords": [...],
    "template": "..."
  }},
  "explanation": "Brief explanation in the same language as the admin's message"
}}"""


def _parse_tier0_assist_response(content: str) -> tuple[dict, str]:
    """Extract suggestion and explanation from LLM response.

    Handles plain JSON and ```json ... ``` code blocks.
    Returns (suggestion_dict, explanation_str).
    """
    # Strip ```json ... ``` or ``` ... ``` wrappers if present.
    stripped = content.strip()
    code_block = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", stripped)
    json_str = code_block.group(1) if code_block else stripped

    try:
        parsed = json.loads(json_str)
        suggestion = parsed.get("suggestion", {})
        explanation = parsed.get("explanation", "")
        return suggestion, explanation
    except json.JSONDecodeError:
        # Try to find any JSON object in the content as a fallback.
        obj_match = re.search(r"\{[\s\S]+\}", stripped)
        if obj_match:
            try:
                parsed = json.loads(obj_match.group(0))
                suggestion = parsed.get("suggestion", {})
                explanation = parsed.get("explanation", "")
                return suggestion, explanation
            except json.JSONDecodeError:
                pass
        # Complete fallback — return empty suggestion with raw content as explanation.
        return {}, content


@router.post("/assist")
async def tier0_assist(
    tenant_id: uuid.UUID,
    body: Tier0AssistRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Use the tenant's configured LLM to suggest a tier0_template configuration
    for a given tool, based on the admin's natural-language description."""
    config = (
        await db.execute(
            select(TenantShellConfig).where(TenantShellConfig.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()

    if not config:
        raise HTTPException(status_code=404, detail="Tenant config not found")

    # Use model_resolver so we get the tenant's currently active model
    # (TenantModelConfig if configured, otherwise shell_config fallback).
    from app.services.llm.model_resolver import resolve_model
    resolved = await resolve_model(
        tenant_id=str(tenant_id),
        user_content=body.user_message,
        db=db,
        shell_config=config,
    )
    provider = resolved.provider
    model_name = resolved.model_name

    system_prompt = _build_tier0_assist_prompt(
        tool_name=body.tool_name,
        tool_description=body.tool_description,
        current_tier0=body.current_tier0,
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": body.user_message},
    ]

    try:
        response = await provider.chat_completion(
            messages,
            model_name,
            temperature=0.3,
            max_tokens=2048,
        )
    except Exception as exc:
        logger.error("tier0_assist: LLM call failed for tenant %s: %s", tenant_id, exc)
        raise HTTPException(status_code=502, detail=f"LLM provider error: {exc}") from exc

    raw = response.content or ""
    suggestion, explanation = _parse_tier0_assist_response(raw)

    return {
        "suggestion": suggestion,
        "explanation": explanation,
        "raw": raw,
    }


# ---------------------------------------------------------------------------
# Tier 0 wizard — multi-example generation + deterministic validation
# ---------------------------------------------------------------------------

class Tier0WizardRequest(BaseModel):
    tool_name: str
    tool_description: str = ""
    positive_examples: list[str] = []
    negative_examples: list[str] = []
    sample_output: str | None = None        # raw JSON returned by the tool
    notes: str | None = None                # free-text guidance
    current_tier0: dict | None = None


def _validate_example(cfg: dict, query: str) -> dict:
    """Replicate the runtime Tier-0 matching against a single query so the
    wizard's preview matches real behaviour 1:1.

    Returns {query, matched, extracted, blocked, reason}.
    """
    from app.services.preprocessing.entities import extract_entities

    query = query.strip()
    if not query:
        return {"query": query, "matched": False, "extracted": None,
                "blocked": False, "reason": "пустой запрос"}

    uq_lower = query.lower()
    block_keywords = cfg.get("block_keywords") or []
    hit = next((bk for bk in block_keywords if bk and bk.lower() in uq_lower), None)
    if hit:
        return {"query": query, "matched": False, "extracted": None,
                "blocked": True, "reason": f"block_keyword «{hit}»"}

    required_entity = (cfg.get("required_entity") or "").lower()

    if required_entity == "keyword_extract":
        kw_regex = cfg.get("keyword_regex") or ""
        if not kw_regex:
            return {"query": query, "matched": False, "extracted": None,
                    "blocked": False, "reason": "keyword_regex не задан"}
        try:
            m = re.match(kw_regex, query, re.IGNORECASE)
        except re.error as exc:
            return {"query": query, "matched": False, "extracted": None,
                    "blocked": False, "reason": f"regex error: {exc}"}
        if not (m and m.lastindex and m.lastindex >= 1):
            return {"query": query, "matched": False, "extracted": None,
                    "blocked": False, "reason": "regex не совпал"}
        extracted = m.group(1).strip()
        for sp in (cfg.get("strip_prefixes") or []):
            if sp and extracted.lower().startswith(sp.lower()):
                extracted = extracted[len(sp):].strip()
                break
        if not extracted:
            return {"query": query, "matched": False, "extracted": None,
                    "blocked": False, "reason": "пустой захват после strip_prefixes"}
        return {"query": query, "matched": True, "extracted": extracted,
                "blocked": False, "reason": "regex совпал"}

    if required_entity:
        plural_map = {"id": "numeric_ids", "email": "emails", "date": "dates",
                      "phone": "phones", "mac": "macs", "ip": "ips"}
        bag_key = plural_map.get(required_entity, required_entity + "s")
        bag = extract_entities(query).as_dict()
        vals = bag.get(bag_key) or []
        if vals:
            return {"query": query, "matched": True, "extracted": vals[0],
                    "blocked": False, "reason": f"найден {required_entity}"}
        return {"query": query, "matched": False, "extracted": None,
                "blocked": False, "reason": f"{required_entity} не найден"}

    # required_entity is null → tool called with no args; always matches.
    return {"query": query, "matched": True, "extracted": None,
            "blocked": False, "reason": "без сущности (вызов без аргументов)"}


def _validate_tier0(cfg: dict, positives: list[str], negatives: list[str]) -> dict:
    """Run cfg against positives (should match) and negatives (should NOT)."""
    pos = []
    for q in positives:
        r = _validate_example(cfg, q)
        r["expected"] = "match"
        r["ok"] = r["matched"]
        pos.append(r)
    neg = []
    for q in negatives:
        r = _validate_example(cfg, q)
        r["expected"] = "skip"
        r["ok"] = not r["matched"]
        neg.append(r)
    all_rows = pos + neg
    passed = sum(1 for r in all_rows if r["ok"])
    return {
        "results": all_rows,
        "passed": passed,
        "total": len(all_rows),
        "all_ok": passed == len(all_rows) and len(all_rows) > 0,
    }


_TIER0_WIZARD_GUIDE = """You are an expert assistant that configures Tier 0 deterministic routing for an AI platform.

## What is Tier 0?

Tier 0 is a deterministic shortcut that bypasses the LLM for simple, repetitive
queries. When a user message matches a pattern, the platform extracts an entity,
calls the tool directly, and renders the result from a markdown template — with no
LLM call. This is generic infrastructure: it works for ANY domain (support desks,
e-commerce, internal IT, billing, logistics — not just one industry).

## Configurable fields

### `required_entity`
What to extract from the query before calling the tool. Supported values (these are
the ONLY ones the runtime extracts — do not invent others):
- `"keyword_extract"` — arbitrary text captured by `keyword_regex` (the capture group
  becomes the tool argument). Use for names, titles, free-text identifiers.
- `"phone"` — a phone number.
- `"email"` — an email address.
- `"ip"` — an IPv4/IPv6 address.
- `"mac"` — a MAC address.
- `"id"` — a bare numeric id / number (order #, ticket #, account #, etc.).
- `"date"` — a date.
- `null` — extract nothing; the tool is called with no arguments (e.g. "show my balance").

### `keyword_regex`
A Python regex with **exactly ONE capture group**, used only when
`required_entity == "keyword_extract"`. It is applied with `re.match` (anchored at the
START of the query) and `re.IGNORECASE`. The capture group holds the value to extract.
- Make the capture group non-greedy when followed by optional trailing tokens.
- If a qualifier noun precedes the value ("order number X", "ticket for X"), include it
  in the regex so it is consumed: `(?:order(?:\\\\s+number)?|ticket\\\\s+for)\\\\s+([\\\\w\\\\s\\\\-\\\\.#]+?)(?:$|[?!.,;])`

### `strip_prefixes`
Strings stripped (case-insensitive) from the START of the captured keyword. Use only
when a preposition/word sits directly before the value and the regex didn't consume it.

### `block_keywords`
Substrings that, if present anywhere in the query, SKIP Tier 0 and send the query to the
LLM. Use for queries that look similar on the surface but carry extra conditions the tool
can't handle deterministically (filters, ranges, lists, conjunctions like "and"/"all").

### `template`
A Markdown string rendered from the tool's JSON output using `{field.path}` placeholders.
Reference ONLY field paths that exist in the sample output (when provided).
For arrays, use an explicit numeric index: if the output is `{"items": [{"name": ...}]}`,
write `{items.0.name}` (NOT `{items.name}`). The same applies to `required_fields`.

### `param_maps`
**Critical — without this the tool is called with NO arguments and Tier 0 silently
fails.** A list of attempts; each attempt is an object mapping a tool parameter path
to a value. Use `$<entity>` to inject the extracted entity:
- `$keyword_extract` — the text captured by `keyword_regex`
- `$phone`, `$email`, `$ip`, `$mac`, `$id`, `$date` — the matched built-in entity
- a plain string is a literal value
Dotted paths set nested params: `{"filters.phone": "$phone"}` → `{"filters": {"phone": ...}}`.
A `|`-suffix runs a format pipeline, e.g. `"$phone|re_sub:^\\\\+38=>"` strips a +38 prefix.
The param path MUST be one of the tool's actual parameters (listed under Tool context).
List several attempts only if the first might miss; usually one attempt is enough.

### `required_fields`
List of dotted paths in the tool output that MUST be non-null for Tier 0 to render
(otherwise it falls through to the LLM). Reference REAL paths from the sample output.

## Examples (domain-neutral)

### A — free-text lookup (keyword_extract)
Queries like "find customer John Smith", "lookup user Jane Doe":
```json
{
  "required_entity": "keyword_extract",
  "keyword_regex": "(?:find|lookup|search)\\\\s+(?:customer|user|client)\\\\s+([\\\\w\\\\s\\\\-\\\\.]+?)(?:$|[?!.,;])",
  "strip_prefixes": [],
  "block_keywords": ["all", "list", "between", "and"],
  "param_maps": [{"query": "$keyword_extract"}],
  "template": "**{name}** — {email}\\n{status}"
}
```
(here the tool has a `query` parameter; the captured text is passed into it.)

### B — numeric id (built-in entity)
Queries like "status of order 10254", "ticket 8841":
```json
{
  "required_entity": "id",
  "keyword_regex": null,
  "strip_prefixes": [],
  "block_keywords": [],
  "param_maps": [{"filters.id": "$id"}],
  "template": "Order **{id}**: {status}\\nTotal: {total}"
}
```

### C — email lookup (built-in entity)
```json
{
  "required_entity": "email",
  "keyword_regex": null,
  "strip_prefixes": [],
  "block_keywords": [],
  "param_maps": [{"email": "$email"}],
  "template": "**{user.name}** ({user.email}) — {user.role}"
}
```
"""


def _tool_param_lines(param_schema: dict | None) -> str:
    """Render a tool's parameter schema as a compact bullet list for the prompt."""
    if not isinstance(param_schema, dict):
        return "(schema unavailable — infer a sensible param name like `query`)"
    props = param_schema.get("properties")
    if not isinstance(props, dict) or not props:
        return "(no parameters)"
    required = set(param_schema.get("required") or [])
    out = []
    for name, spec in props.items():
        spec = spec if isinstance(spec, dict) else {}
        typ = spec.get("type", "any")
        desc = spec.get("description", "")
        req = " (required)" if name in required else ""
        line = f"  - `{name}`: {typ}{req}"
        if desc:
            line += f" — {desc[:120]}"
        out.append(line)
        # one nesting level for object params (so filters.* paths are visible)
        sub = spec.get("properties")
        if isinstance(sub, dict):
            for sn in sub:
                out.append(f"      - `{name}.{sn}`")
    return "\n".join(out)


def _build_tier0_wizard_prompt(body: Tier0WizardRequest, failures: list[dict] | None,
                               param_schema: dict | None = None) -> str:
    base = _TIER0_WIZARD_GUIDE

    pos = "\n".join(f"  - {q}" for q in body.positive_examples if q.strip()) or "  (none)"
    neg = "\n".join(f"  - {q}" for q in body.negative_examples if q.strip()) or "  (none)"
    sample = (body.sample_output or "").strip()
    sample_block = f"```json\n{sample[:4000]}\n```" if sample else "(not provided)"
    notes_block = (body.notes or "").strip() or "(none)"

    fail_block = ""
    if failures:
        lines = []
        for f in failures:
            exp = f.get("expected")
            q = f.get("query")
            if exp == "match":
                lines.append(f"  - SHOULD have matched but did NOT: «{q}» (reason: {f.get('reason')})")
            else:
                lines.append(f"  - SHOULD have been skipped but MATCHED: «{q}» (extracted: {f.get('extracted')})")
        fail_block = (
            "\n## Previous attempt failed these cases — FIX them\n"
            "Your last config produced wrong results on the cases below. "
            "Adjust keyword_regex / required_entity / strip_prefixes / block_keywords "
            "so EVERY positive example matches and EVERY negative example is skipped:\n"
            + "\n".join(lines) + "\n"
        )

    return f"""{base}
## Tool context

**Tool name:** {body.tool_name}
**Tool description:** {body.tool_description}

**Tool parameters (use these exact paths in `param_maps`):**
{_tool_param_lines(param_schema)}

## Wizard inputs

The admin provided concrete examples instead of a single description. Design the
config so it generalises across ALL positive examples and rejects ALL negatives.

### Positive examples — queries that SHOULD trigger this tool via Tier 0
{pos}

### Negative examples — queries that must FALL THROUGH to the LLM (skip Tier 0)
{neg}

### Sample tool output (use REAL field paths from this when writing `template`)
{sample_block}

### Additional notes from the admin
{notes_block}
{fail_block}
## Your task

Produce a `tier0_template` that:
1. Matches every positive example (pick `required_entity` and, for keyword_extract,
   a `keyword_regex` with ONE capture group that works for ALL positives).
2. Does NOT match any negative example (use `block_keywords` for surface-similar
   queries that carry extra conditions).
3. **Sets `param_maps`** so the extracted entity is passed into a REAL tool parameter
   from the list above (this is mandatory — a config without param_maps cannot fire).
4. Has a `template` referencing only fields that exist in the sample output above,
   and `required_fields` listing the key paths that must be present. If no sample
   output, write a reasonable template and leave required_fields empty.
5. Provides a `not_found_template` — a short message for when the tool returns no
   record (empty result). Reference the query value, e.g. `{{keyword_extract}}` /
   `{{phone}}` / `{{query}}`. Write it in the language of the admin's examples.

Respond ONLY with a JSON object (no markdown wrapper) in exactly this format:
{{
  "suggestion": {{
    "required_entity": "...",
    "keyword_regex": "...",
    "strip_prefixes": [...],
    "block_keywords": [...],
    "param_maps": [{{"<tool_param>": "$<entity>"}}],
    "required_fields": [...],
    "template": "...",
    "not_found_template": "..."
  }},
  "explanation": "Brief explanation in the same language as the admin's examples"
}}"""


@router.post("/wizard")
async def tier0_wizard(
    tenant_id: uuid.UUID,
    body: Tier0WizardRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Generate a full tier0_template from worked examples, then deterministically
    validate the generated regex/entity against those same examples so the admin
    sees per-example pass/fail before applying."""
    config = (
        await db.execute(
            select(TenantShellConfig).where(TenantShellConfig.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Tenant config not found")

    if not any(q.strip() for q in body.positive_examples):
        raise HTTPException(status_code=400, detail="Нужен хотя бы один пример-запрос.")

    # Tool parameter schema — so the LLM can wire param_maps to real params.
    param_schema: dict | None = None
    if body.tool_name:
        from app.models.tenant_tool import TenantTool
        tool = (await db.execute(
            select(TenantTool).where(TenantTool.tenant_id == tenant_id,
                                     TenantTool.name == body.tool_name)
        )).scalars().first()
        if tool and isinstance(tool.config_json, dict):
            fn = tool.config_json.get("function") or {}
            param_schema = fn.get("parameters") or tool.config_json.get("parameters")

    # Detect a refine pass: caller passes current_tier0 = the previous suggestion
    # and we re-validate it to tell the LLM what to fix.
    failures: list[dict] | None = None
    if body.current_tier0:
        prev_val = _validate_tier0(
            body.current_tier0, body.positive_examples, body.negative_examples
        )
        failures = [r for r in prev_val["results"] if not r["ok"]] or None

    from app.services.llm.model_resolver import resolve_model
    resolved = await resolve_model(
        tenant_id=str(tenant_id),
        user_content=" ".join(body.positive_examples)[:500],
        db=db,
        shell_config=config,
    )

    system_prompt = _build_tier0_wizard_prompt(body, failures, param_schema)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "Сгенерируй tier0_template по примерам выше."},
    ]

    try:
        response = await resolved.provider.chat_completion(
            messages, resolved.model_name, temperature=0.2, max_tokens=2048,
        )
    except Exception as exc:
        logger.error("tier0_wizard: LLM call failed for tenant %s: %s", tenant_id, exc)
        raise HTTPException(status_code=502, detail=f"LLM provider error: {exc}") from exc

    raw = response.content or ""
    suggestion, explanation = _parse_tier0_assist_response(raw)

    validation = _validate_tier0(
        suggestion or {}, body.positive_examples, body.negative_examples
    )

    return {
        "suggestion": suggestion,
        "explanation": explanation,
        "validation": validation,
        "raw": raw,
    }


# ---------------------------------------------------------------------------
# Tier 0 test bench — explain decision + run full LLM pipeline
# ---------------------------------------------------------------------------

class Tier0ExplainRequest(BaseModel):
    query: str
    focus_tool: str | None = None
    run_tool: bool = True
    override_tier0: dict | None = None  # unsaved editor/wizard config to test


@router.post("/explain")
async def tier0_explain(
    tenant_id: uuid.UUID,
    body: Tier0ExplainRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Full read-only trace of the Tier 0 decision for a query: entity extraction,
    semantic ranking, competing regex matches, the winning/blocking gate, and
    recommendations. Optionally executes the matched tool to test rendering."""
    config = (
        await db.execute(
            select(TenantShellConfig).where(TenantShellConfig.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Tenant config not found")
    if not body.query.strip():
        raise HTTPException(status_code=400, detail="Пустой запрос.")

    from app.services.llm.tier0_router import explain_tier0
    trace = await explain_tier0(
        tenant_id=str(tenant_id),
        user_query=body.query,
        db=db,
        embedding_model=getattr(config, "embedding_model_name", None),
        min_tool_score=float(getattr(config, "tier0_min_tool_score", 0.80) or 0.80),
        max_score_gap=float(getattr(config, "tier0_max_score_gap", 0.15) or 0.15),
        focus_tool=body.focus_tool,
        run_tool=body.run_tool,
        override_tier0=body.override_tier0,
    )
    trace["tenant_tier0_enabled"] = bool(getattr(config, "tier0_enabled", False))
    return trace


class Tier0TestLLMRequest(BaseModel):
    query: str


@router.post("/test-llm")
async def tier0_test_llm(
    tenant_id: uuid.UUID,
    body: Tier0TestLLMRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Run the query through the FULL pipeline (Tier 0 → LLM chain) in a scratch
    chat that is deleted afterwards, so the admin can compare what actually serves
    the answer (tier0 / model / tools) without polluting real chats or stats."""
    if not body.query.strip():
        raise HTTPException(status_code=400, detail="Пустой запрос.")

    from app.models.chat import Chat
    from app.models.message import Message
    from app.models.llm_request_log import LLMRequestLog
    from app.services.llm.pipeline import chat_completion

    # Scratch chat — marked so it never shows in normal lists and is cleaned up.
    chat = Chat(tenant_id=tenant_id, title="🔧 Tier0 test (scratch)")
    db.add(chat)
    await db.flush()
    user_msg = Message(tenant_id=tenant_id, chat_id=chat.id, role="user",
                       content=body.query, status="sent")
    db.add(user_msg)
    await db.flush()
    await db.commit()

    events: list[dict] = []

    async def _sink(event_type: str, payload: dict) -> None:
        events.append({"type": event_type, "payload": payload})

    result: dict = {}
    error: str | None = None
    try:
        result = await chat_completion(
            tenant_id=str(tenant_id), chat_id=str(chat.id),
            user_content=body.query, db=db,
            user_message_id=str(user_msg.id), on_event=_sink,
        )
    except Exception as exc:
        logger.exception("tier0_test_llm: pipeline failed")
        error = str(exc)
    finally:
        # Cleanup: delete scratch chat + messages + any log rows it produced.
        try:
            await db.execute(
                LLMRequestLog.__table__.delete().where(LLMRequestLog.chat_id == chat.id)
            )
            await db.execute(Message.__table__.delete().where(Message.chat_id == chat.id))
            await db.execute(Chat.__table__.delete().where(Chat.id == chat.id))
            await db.commit()
        except Exception:
            logger.warning("tier0_test_llm: scratch cleanup failed", exc_info=True)
            await db.rollback()

    if error:
        raise HTTPException(status_code=502, detail=f"Ошибка пайплайна: {error}")

    served_by = "tier0" if (result.get("model_name") == "tier0" or result.get("tier0")) else "llm"
    tool_events = [e for e in events if e["type"] in ("tool_call", "tool_result", "tier0_hit")]
    return {
        "served_by": served_by,
        "content": result.get("content", ""),
        "model_name": result.get("model_name"),
        "provider_type": result.get("provider_type"),
        "tool_calls_count": result.get("tool_calls_count", 0),
        "total_tokens": result.get("total_tokens"),
        "prompt_tokens": result.get("prompt_tokens"),
        "completion_tokens": result.get("completion_tokens"),
        "latency_ms": result.get("latency_ms"),
        "tier0": result.get("tier0"),
        "reasoning": result.get("reasoning"),
        "events": tool_events,
    }
