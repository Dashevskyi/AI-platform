"""Semantic / keyword / LLM tool selection (extracted from pipeline)."""
from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

MAX_TOOLS_PER_REQUEST = 20    # ≤ → send all; > → use semantic selection
TOOL_KEYWORD_THRESHOLD = 80   # use keyword matching up to this; semantic above
TOOL_SEMANTIC_TOPK = 18       # how many tools to pull from semantic search
LOCAL_QWEN_TOOL_BUDGET = 8
DEFAULT_TOOL_BUDGET = 12


def tool_budget_for_model(model_name: str | None) -> int:
    lowered = (model_name or "").lower()
    if "qwen2.5" in lowered or "qwen2_5" in lowered:
        return LOCAL_QWEN_TOOL_BUDGET
    return DEFAULT_TOOL_BUDGET


_TOPIC_TOKEN_RE = re.compile(r"[A-Za-zА-Яа-яЁёІіЇїЄєҐґ0-9][A-Za-zА-Яа-яЁёІіЇїЄєҐґ0-9\.-]{2,}")


def _topic_tokens(text: str) -> set[str]:
    if not text:
        return set()
    tokens: set[str] = set()
    for raw in _TOPIC_TOKEN_RE.findall(text.lower()):
        token = raw.strip(".,:;!?()[]{}\"'")
        if len(token) < 3:
            continue
        tokens.add(token)
    return tokens


def _should_carry_tool_history(current_user_content: str, prior_user_content: str) -> bool:
    """Keep the latest prior tool turn only when it still looks relevant.

    Without this filter, unrelated requests can inherit a stale tool task from the
    previous turn. That is especially harmful because tools are enabled for almost
    every substantive message, so a fresh question like certificate setup may end up
    carrying an old address/geocoding request into the next prompt.
    """
    current = (current_user_content or "").strip()
    prior = (prior_user_content or "").strip()
    if not current or not prior:
        return False

    current_distinctive = _extract_distinctive_tokens(current)
    prior_distinctive = _extract_distinctive_tokens(prior)
    if current_distinctive & prior_distinctive:
        return True

    current_tokens = _topic_tokens(current)
    prior_tokens = _topic_tokens(prior)
    overlap = current_tokens & prior_tokens
    if len(overlap) >= 2:
        return True

    # Stronger tie for addresses / domains / hostnames where a single shared token
    # can still be sufficient (`gagarina`, `ai.it-invest.ua`).
    if len(overlap) == 1:
        only = next(iter(overlap))
        if "." in only or only.isdigit():
            return True
        if only in {"гагарина", "університетський", "университетский", "кривий", "кривой"}:
            return True

    return False


def _compact_history_for_tool_request(
    history_dicts: list[dict],
    current_user_content: str,
    max_user_turns: int = 1,
) -> list[dict]:
    if not history_dicts:
        return []
    selected: list[dict] = []
    user_turns = 0
    pending_assistant_summary: dict | None = None
    for msg in reversed(history_dicts):
        role = msg.get("role")
        content = msg.get("content")
        if role == "user":
            compact = _compact_text(content if isinstance(content, str) else str(content), max_chars=300)
            if compact and _should_carry_tool_history(current_user_content, compact):
                if pending_assistant_summary is not None:
                    selected.append(pending_assistant_summary)
                    pending_assistant_summary = None
                selected.append({"role": "user", "content": compact})
                user_turns += 1
            if user_turns >= max_user_turns:
                break
            continue
        if role == "assistant" and isinstance(content, str) and content.startswith("[Крат"):
            compact = _compact_text(content, max_chars=500)
            if compact:
                pending_assistant_summary = {"role": "assistant", "content": compact}
    selected.reverse()
    return selected


async def select_relevant_tools(
    all_tools: list,
    user_message: str,
    provider,
    model_name: str,
    *,
    embedding_model: str | None = None,
    db = None,
    tenant_id: str | None = None,
    semantic_floor: float = 0.5,
    query_vector: list[float] | None = None,
    ontology_examples: list[dict] | None = None,
    semantic_cache: list | None = None,
) -> list:
    """
    Select relevant tools for the user message:

      • ≤ MAX_TOOLS_PER_REQUEST  → return everything (no filter needed)
      • > MAX_TOOLS_PER_REQUEST → prefer semantic search via tool embeddings,
        falling back to keyword matcher → LLM-pick when embeddings are absent.

    Pinned tools (is_pinned=True) are always included on top of the selection.

    Semantic is the default above the cap because keyword matching scores
    English tool names poorly against Russian queries, and reliably drops
    legitimately relevant tools (observed: `search_tasks` dropped because the
    user message lacked literal "tasks"/"задания" tokens even though the topic
    was billing tasks).
    """
    if not all_tools:
        return []

    budget = min(MAX_TOOLS_PER_REQUEST, tool_budget_for_model(model_name))

    # Tier 1 — small set, send everything when the budget allows it.
    if len(all_tools) <= budget:
        return all_tools

    pinned = [t for t in all_tools if getattr(t, "is_pinned", False)]
    pinned_ids = {t.id for t in pinned}
    rest = [t for t in all_tools if t.id not in pinned_ids]
    # Tag pinned tools with their selection source — used by debug-trace.
    for t in pinned:
        t._selection_source = "pinned"

    selected: list = []
    selection_method = ""

    # Tier 2 — semantic search when embeddings available. Domain workflows
    # (e.g. PON: pon_search → pon_tree) belong in tenant.ontology_prompt as
    # plain instructions, not as a hardcoded route here — keeping selection
    # purely semantic keeps the pipeline tenant-agnostic.
    embeddable = [t for t in rest if getattr(t, "embedding", None) is not None]
    has_enough_embeddings = embedding_model and db is not None and tenant_id and len(embeddable) >= len(rest) // 2
    semantic_selected: list = []
    non_embedded_fallback: list = []
    # True once semantic search actually executed. When it did, an empty result
    # (everything below the floor) is a DELIBERATE "no relevant tools" — for
    # conversational/identity queries ("кто ты?") we must NOT escalate to the
    # keyword/LLM-pick fallbacks, which would force-pick an irrelevant tool.
    semantic_ran = False
    if has_enough_embeddings:
        try:
            from app.services.tools.embedder import search_tools
            if semantic_cache is not None:
                semantic_results = semantic_cache
                semantic_ran = True
            else:
                semantic_results = await search_tools(
                    tenant_id=str(tenant_id),
                    query=user_message,
                    db=db,
                    embedding_model=embedding_model,
                    top_k=TOOL_SEMANTIC_TOPK,
                    query_vector=query_vector,
                    ontology_examples=ontology_examples,
                )
                semantic_ran = True
            if semantic_results:
                # Apply per-tenant similarity floor — tools below it are noisy
                # "kinda matches" that crowd the prompt without adding signal.
                # Non-embedded tools bypass this floor (we can't score them).
                semantic_filtered = [
                    t for t in semantic_results
                    if (getattr(t, "_semantic_score", None) or 0.0) >= float(semantic_floor or 0.0)
                ]
                semantic_ids = {t.id for t in semantic_filtered}
                non_embedded_fallback = [t for t in rest if getattr(t, "embedding", None) is None and t.id not in semantic_ids]
                for t in semantic_filtered:
                    t._selection_source = "semantic"
                    # _semantic_score already set by search_tools
                for t in non_embedded_fallback:
                    t._selection_source = "non-embedded-fallback"
                semantic_selected = semantic_filtered
                if len(semantic_filtered) < len(semantic_results):
                    logger.info(
                        "[tool-select] semantic floor %.2f cut %d/%d tools (kept %d)",
                        semantic_floor, len(semantic_results) - len(semantic_filtered),
                        len(semantic_results), len(semantic_filtered),
                    )
        except Exception:
            logger.exception("semantic tool selection failed; falling back to keyword")

    # Merge semantic + non-embedded fallback — dedup by tool id.
    if semantic_selected or non_embedded_fallback:
        seen_merge: set = set()
        for src in (semantic_selected, non_embedded_fallback):
            for t in src:
                if t.id in seen_merge:
                    continue
                seen_merge.add(t.id)
                selected.append(t)
        parts = []
        if semantic_selected:
            parts.append("semantic")
        if not parts and non_embedded_fallback:
            parts.append("non-embedded-fallback")
        selection_method = "+".join(parts)

    # Tier 3 — keyword fallback (only when semantic search couldn't run, i.e.
    # tenant lacks embeddings). If semantic ran and kept nothing, that's a
    # deliberate "no tools" — don't force a keyword/LLM pick.
    if not selected and not semantic_ran and len(rest) <= TOOL_KEYWORD_THRESHOLD:
        try:
            selected = keyword_match_tools(rest, user_message)
            for t in selected:
                t._selection_source = "keyword"
            selection_method = "keyword"
        except Exception:
            logger.exception("keyword tool selection failed")

    # Tier 4 — last resort LLM pick, also only when semantic didn't run.
    if not selected and not semantic_ran:
        try:
            selected = await llm_select_tools(rest, user_message, provider, model_name)
            for t in selected:
                t._selection_source = "llm-pick"
            selection_method = "llm-pick"
        except Exception:
            selected = []
            selection_method = "fallback-empty"

    if not selected and semantic_ran and not selection_method:
        selection_method = "semantic-empty"  # no tool cleared the floor — answer directly

    # Pinned tools are "system-essentials" (memory/artifacts/RAG helpers).
    # They go in ABOVE the budget — budget only constrains the non-pinned
    # semantic/keyword selection. Otherwise pinned starves out the
    # actually-relevant tools for the user query (observed: 7 pinned filled
    # the 8-slot Qwen budget and squeezed out `ping` for a network query).
    seen_ids: set = set()
    selected_non_pinned: list = []
    for t in selected:
        if t.id in pinned_ids or t.id in seen_ids:
            continue
        seen_ids.add(t.id)
        selected_non_pinned.append(t)
    # Budget cap applies only to non-pinned. Final payload = pinned + capped.
    capped_non_pinned = selected_non_pinned[:budget]
    final: list = [*pinned, *capped_non_pinned]
    logger.info(
        "tool selection: tenant=%s total=%d pinned=%d %s -> %d non-pinned kept (budget=%d) + %d pinned = %d total",
        tenant_id, len(all_tools), len(pinned), selection_method,
        len(capped_non_pinned), budget, len(pinned), len(final),
    )
    return final


def keyword_match_tools(all_tools: list, user_message: str) -> list:
    """Score tools by keyword overlap with user message."""
    msg_lower = user_message.lower()
    msg_words = set(msg_lower.split())

    scored = []
    for tool in all_tools:
        score = 0
        name = (tool.name or "").lower()
        desc = (tool.description or "").lower()
        tags = " ".join(tool_capability_tags(tool)).lower()
        # Name match is strong signal
        if name in msg_lower:
            score += 10
        # Word overlap
        tool_words = set(name.split("_")) | set(name.split("-")) | set(desc.split()) | set(tags.split())
        overlap = msg_words & tool_words
        score += len(overlap) * 2
        # Partial substring match in description
        for word in msg_words:
            if len(word) > 3 and word in desc:
                score += 1
            if len(word) > 3 and word in tags:
                score += 1
        scored.append((score, tool))

    scored.sort(key=lambda x: x[0], reverse=True)
    # Return top tools with score > 0, up to MAX_TOOLS_PER_REQUEST
    selected = [t for score, t in scored[:MAX_TOOLS_PER_REQUEST] if score > 0]
    # If nothing matched, return top N by name (better than nothing)
    if not selected:
        selected = [t for _, t in scored[:MAX_TOOLS_PER_REQUEST]]
    return selected


TOOL_SELECTION_PROMPT = """У тебя есть список инструментов. Пользователь отправил сообщение.
Выбери ТОЛЬКО те инструменты, которые могут понадобиться для ответа на это сообщение.
Верни JSON-массив с именами выбранных инструментов (максимум {max_tools}).
Если ни один инструмент не нужен — верни [].

Инструменты:
{tools_list}

Сообщение пользователя: {user_message}

JSON-массив имён:"""


async def llm_select_tools(
    all_tools: list,
    user_message: str,
    provider,
    model_name: str,
) -> list:
    """Use LLM to select relevant tools from a large set."""
    tools_summary = "\n".join(
        f"- {t.name} [{', '.join(tool_capability_tags(t)) or 'no-tags'}]: {(t.description or 'нет описания')[:100]}"
        for t in all_tools
    )

    prompt = TOOL_SELECTION_PROMPT.format(
        max_tools=MAX_TOOLS_PER_REQUEST,
        tools_list=tools_summary[:3000],
        user_message=user_message[:500],
    )

    resp = await provider.chat_completion(
        messages=[{"role": "user", "content": prompt}],
        model=model_name,
        temperature=0.0,
        max_tokens=200,
    )

    text = resp.content.strip()
    if "```" in text:
        import re
        match = re.search(r'```(?:json)?\s*(\[.*?\])\s*```', text, re.DOTALL)
        if match:
            text = match.group(1)

    selected_names = json.loads(text)
    if not isinstance(selected_names, list):
        return keyword_match_tools(all_tools, user_message)

    name_set = set(str(n).lower().strip() for n in selected_names)
    selected = [t for t in all_tools if t.name.lower().strip() in name_set]

    logger.debug(f"LLM tool selection: {len(selected)}/{len(all_tools)} tools selected")
    return selected[:MAX_TOOLS_PER_REQUEST]


def tool_capability_tags(tool) -> list[str]:
    config = getattr(tool, "config_json", None)
    if not isinstance(config, dict):
        return []
    runtime = config.get("x_backend_config")
    if not isinstance(runtime, dict):
        return []
    tags = runtime.get("capability_tags")
    if not isinstance(tags, list):
        return []
    return [str(tag).strip() for tag in tags if str(tag).strip()]
