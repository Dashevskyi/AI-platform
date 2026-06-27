"""Preflight planner — heuristic + optional LLM for borderline queries."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from app.core.config import settings

logger = logging.getLogger(__name__)

_CHITCHAT = {
    "привет", "здравствуй", "здравствуйте", "добрый день", "добрый вечер",
    "доброе утро", "хай", "hi", "hello",
    "спасибо", "благодарю", "thanks", "thank you",
    "пока", "до свидания", "до встречи", "bye",
    "ок", "окей", "ok", "okay", "хорошо", "понятно", "понял", "ясно",
    "да", "нет", "ага", "угу", "yes", "no",
    "круто", "отлично", "супер", "класс",
}

LLM_PREFLIGHT_PROMPT = """Decide which context subsystems are needed for this user message.
Reply with ONLY JSON (no markdown):
{{"need_memory": true|false, "need_kb": true|false, "need_grounding": true|false, "need_tools": true|false}}

Rules:
- Greetings/thanks/yes-no → mostly false except grounding if follow-up likely
- Technical/data lookup questions → need_tools true, usually need_kb true
- Short follow-ups referencing prior context → need_grounding true

User message: {query}"""


@dataclass
class PreflightPlan:
    need_memory_semantic: bool = True
    need_kb: bool = True
    need_grounding: bool = True
    need_semantic_tools: bool = True
    reason: str = "default"
    mode: str = "heuristic"


def plan_preflight_heuristic(
    query: str,
    *,
    has_attachments: bool = False,
    memory_enabled: bool = True,
    kb_enabled: bool = True,
    kb_inject_auto: bool = True,
    tools_likely: bool = True,
) -> PreflightPlan:
    text = (query or "").strip()
    lowered = text.lower()

    plan = PreflightPlan(
        need_memory_semantic=memory_enabled,
        need_kb=kb_enabled and kb_inject_auto,
        need_grounding=True,
        need_semantic_tools=tools_likely,
    )

    if has_attachments:
        plan.reason = "attachments_present"
        return plan

    if not kb_inject_auto:
        plan.need_kb = False

    if not memory_enabled:
        plan.need_memory_semantic = False

    if len(text) < 4:
        plan.need_memory_semantic = False
        plan.need_kb = False
        plan.need_grounding = False
        plan.need_semantic_tools = False
        plan.reason = "too_short"
        return plan

    stripped = re.sub(r"[\s\.\!\?\,\;\:\)\(\-—…]+$", "", lowered)
    stripped = re.sub(r"^[\s\.\!\?\,\;\:\)\(\-—…]+", "", stripped)
    if stripped in _CHITCHAT:
        plan.need_memory_semantic = False
        plan.need_kb = False
        plan.need_grounding = len(text) >= 12
        plan.need_semantic_tools = False
        plan.reason = "chitchat_exact"
        return plan

    if len(text) < 12:
        for pat in _CHITCHAT:
            if stripped.startswith(pat) and len(stripped) <= len(pat) + 3:
                plan.need_memory_semantic = False
                plan.need_kb = False
                plan.need_grounding = False
                plan.need_semantic_tools = False
                plan.reason = "chitchat_prefix"
                return plan

    if not tools_likely:
        plan.need_semantic_tools = False
        plan.reason = "tools_not_needed"

    return plan


def plan_preflight_off(
    *,
    memory_enabled: bool = True,
    kb_enabled: bool = True,
    kb_inject_auto: bool = True,
    tools_likely: bool = True,
) -> PreflightPlan:
    """Disable planner gating — load everything enabled by tenant config."""
    return PreflightPlan(
        need_memory_semantic=memory_enabled,
        need_kb=kb_enabled and kb_inject_auto,
        need_grounding=True,
        need_semantic_tools=tools_likely,
        reason="planner_off",
        mode="off",
    )


async def plan_preflight_llm(
    query: str,
    provider,
    model_name: str,
    *,
    base: PreflightPlan,
    memory_enabled: bool = True,
    kb_enabled: bool = True,
    kb_inject_auto: bool = True,
    tools_likely: bool = True,
) -> PreflightPlan:
    """Refine heuristic plan with a tiny LLM call (borderline queries only)."""
    if base.reason not in ("default", "tools_not_needed"):
        base.mode = "llm_skipped"
        return base
    q = (query or "").strip()
    if len(q) < 16 or len(q) > 400:
        base.mode = "llm_skipped"
        return base
    try:
        resp = await provider.chat_completion(
            messages=[{"role": "user", "content": LLM_PREFLIGHT_PROMPT.format(query=q[:400])}],
            model=model_name,
            temperature=0.0,
            max_tokens=80,
            extra_body={"chat_template_kwargs": {"enable_thinking": False, "thinking": False}},
        )
        raw = (resp.content or "").strip()
        if "```" in raw:
            raw = raw.replace("```json", "").replace("```", "").strip()
        data = json.loads(raw)
        if not isinstance(data, dict):
            return base
        plan = PreflightPlan(
            need_memory_semantic=bool(data.get("need_memory", base.need_memory_semantic)) and memory_enabled,
            need_kb=bool(data.get("need_kb", base.need_kb)) and kb_enabled and kb_inject_auto,
            need_grounding=bool(data.get("need_grounding", base.need_grounding)),
            need_semantic_tools=bool(data.get("need_tools", base.need_semantic_tools)) and tools_likely,
            reason="llm_planner",
            mode="llm",
        )
        return plan
    except Exception:
        logger.exception("LLM preflight planner failed; using heuristic")
        base.mode = "llm_fallback"
        return base


async def resolve_preflight_plan(
    query: str,
    *,
    mode: str | None = None,
    provider=None,
    model_name: str | None = None,
    has_attachments: bool = False,
    memory_enabled: bool = True,
    kb_enabled: bool = True,
    kb_inject_auto: bool = True,
    tools_likely: bool = True,
) -> PreflightPlan:
    """Entry point: heuristic | llm | off (from settings or override)."""
    effective = (mode or settings.PREFLIGHT_PLANNER_MODE or "heuristic").strip().lower()
    if effective == "off":
        return plan_preflight_off(
            memory_enabled=memory_enabled,
            kb_enabled=kb_enabled,
            kb_inject_auto=kb_inject_auto,
            tools_likely=tools_likely,
        )
    base = plan_preflight_heuristic(
        query,
        has_attachments=has_attachments,
        memory_enabled=memory_enabled,
        kb_enabled=kb_enabled,
        kb_inject_auto=kb_inject_auto,
        tools_likely=tools_likely,
    )
    if effective == "llm" and provider and model_name:
        return await plan_preflight_llm(
            query, provider, model_name,
            base=base,
            memory_enabled=memory_enabled,
            kb_enabled=kb_enabled,
            kb_inject_auto=kb_inject_auto,
            tools_likely=tools_likely,
        )
    base.mode = "heuristic"
    return base


# Backward-compatible alias
plan_preflight = plan_preflight_heuristic
