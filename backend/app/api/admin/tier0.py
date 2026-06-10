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
