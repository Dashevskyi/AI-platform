"""Collect erroneous tool-call signals for ontology improvement.

Sources:
  - production LLM request logs (tool errors, missed/wrong routing)
  - failed assistant audit-suite cases
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assistant import Assistant
from app.models.assistant_audit import AssistantAuditCase
from app.models.llm_request_log import LLMRequestLog

META_TOOLS = {
    "search_kb", "recall_memory", "recall_chat", "describe_tool", "plan",
    "plan_update", "memory_save", "get_artifact", "find_artifacts", "get_message",
}

FAILURE_LABELS = {
    "tool_error": "Ошибка выполнения tool",
    "no_tool_call": "Tool не вызван",
    "wrong_tool": "Вызван другой tool",
    "unexpected_tool": "Лишний вызов tool",
}


def _query_from_log(log: LLMRequestLog) -> str:
    dbg = log.debug if isinstance(log.debug, dict) else {}
    uq = dbg.get("user_query")
    if isinstance(uq, str) and uq.strip():
        return " ".join(uq.split())[:600]
    for src in (log.normalized_request, log.raw_request):
        if not isinstance(src, dict):
            continue
        msgs = src.get("messages")
        if not isinstance(msgs, list):
            continue
        for m in reversed(msgs):
            if not isinstance(m, dict) or m.get("role") != "user":
                continue
            content = m.get("content")
            text_val = None
            if isinstance(content, str):
                text_val = content
            elif isinstance(content, list):
                parts = [
                    p.get("text") for p in content
                    if isinstance(p, dict) and p.get("type") == "text" and p.get("text")
                ]
                text_val = " ".join(parts) if parts else None
            if text_val and text_val.strip():
                t = " ".join(text_val.split())
                return t[:600]
    return ""


def _semantic_ranking(dbg: dict) -> tuple[list[dict], list[str]]:
    payload = dbg.get("tools_payload") or []
    semantic: list[dict] = []
    offered: list[str] = []
    for t in payload:
        if not isinstance(t, dict) or not t.get("name"):
            continue
        name = str(t["name"])
        offered.append(name)
        if (t.get("source") or "") == "builtin":
            continue
        sim = t.get("similarity")
        if isinstance(sim, (int, float)):
            semantic.append({"name": name, "score": round(float(sim), 3)})
    semantic.sort(key=lambda x: -x["score"])
    return semantic[:8], offered[:12]


def _called_tools(dbg: dict, model_name: str | None) -> set[str]:
    tcs = [tc for tc in (dbg.get("tool_calls") or []) if isinstance(tc, dict) and tc.get("name")]
    called = {str(tc["name"]) for tc in tcs}
    if model_name == "tier0":
        t0 = (dbg.get("tier0") or {}).get("tool")
        if t0:
            called.add(str(t0))
    return called


def _classify_audit_case(expected_tools: list[str] | None, called: list[str]) -> str:
    expected = expected_tools or []
    called_set = set(called or [])
    biz = called_set - META_TOOLS
    if not expected:
        return "unexpected_tool" if biz else "pass"
    exp = expected[0]
    if exp and not called_set:
        return "no_tool_call"
    variants = set(exp.split("|"))
    if not (variants & called_set):
        return "wrong_tool" if biz else "no_tool_call"
    return "pass"


def _suggestion(failure_class: str, *, expected: str | None, called: list[str], semantic_top: list[dict]) -> str:
    top = semantic_top[0]["name"] if semantic_top else None
    score = semantic_top[0]["score"] if semantic_top else None
    called_s = ", ".join(called) if called else "—"
    if failure_class == "tool_error":
        return f"Tool `{expected or called[0] if called else '?'}` падает на этом запросе — уточните пример и глоссарий."
    if failure_class == "no_tool_call":
        hint = expected or top
        extra = f" (semantic top: `{top}` {score})" if top and top != hint else ""
        return f"Модель не вызвала tool. Добавьте пример с expected_tool=`{hint or '?'}`{extra}."
    if failure_class == "wrong_tool":
        return (
            f"Ожидался `{expected or top or '?'}`, вызвано: {called_s}. "
            f"Уточните глоссарий и примеры, чтобы отличить от конкурентов."
        )
    if failure_class == "unexpected_tool":
        return f"Вызваны tools без ожидания: {called_s}. Добавьте negative-пример или уточните routing."
    return "Проверьте описание tools и примеры запросов."


def _classify_log_row(log: LLMRequestLog) -> tuple[str | None, dict[str, Any]]:
    dbg = log.debug if isinstance(log.debug, dict) else {}
    model_name = log.model_name
    served_by = log.served_by or "llm"
    tcs = [tc for tc in (dbg.get("tool_calls") or []) if isinstance(tc, dict) and tc.get("name")]
    called = _called_tools(dbg, model_name)
    biz = called - META_TOOLS
    semantic_top, offered = _semantic_ranking(dbg)

    failed = [str(tc["name"]) for tc in tcs if tc.get("ok") is False]
    if failed:
        return "tool_error", {
            "expected_tool": failed[0],
            "called": sorted(called),
            "semantic_top": semantic_top,
            "tools_offered": offered,
        }

    ctx_tools = log.context_tools_count or 0
    if ctx_tools > 0 and not biz and served_by not in ("tier0_template",) and model_name != "tier0":
        top = semantic_top[0] if semantic_top else None
        expected = top["name"] if top and top["score"] >= 0.42 else None
        return "no_tool_call", {
            "expected_tool": expected,
            "called": [],
            "semantic_top": semantic_top,
            "tools_offered": offered,
        }

    if biz and semantic_top:
        top = semantic_top[0]
        if top["score"] >= 0.55 and top["name"] not in called:
            return "wrong_tool", {
                "expected_tool": top["name"],
                "called": sorted(biz),
                "semantic_top": semantic_top,
                "tools_offered": offered,
            }

    return None, {}


def _item_dict(
    *,
    item_id: str,
    source: str,
    query: str,
    failure_class: str,
    expected_tool: str | None,
    called: list[str],
    semantic_top: list[dict],
    tools_offered: list[str],
    created_at: datetime | None = None,
    log_id: str | None = None,
    case_id: str | None = None,
    assistant_id: str | None = None,
    assistant_name: str | None = None,
) -> dict:
    return {
        "id": item_id,
        "source": source,
        "query": query,
        "expected_tool": expected_tool,
        "called": called,
        "tools_offered": tools_offered,
        "semantic_top": semantic_top,
        "failure_class": failure_class,
        "failure_label": FAILURE_LABELS.get(failure_class, failure_class),
        "suggestion": _suggestion(
            failure_class,
            expected=expected_tool,
            called=called,
            semantic_top=semantic_top,
        ),
        "created_at": created_at.isoformat() if created_at else None,
        "log_id": log_id,
        "case_id": case_id,
        "assistant_id": assistant_id,
        "assistant_name": assistant_name,
    }


async def collect_tool_call_audit(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    days: int = 14,
    limit: int = 80,
    include_logs: bool = True,
    include_audit_cases: bool = True,
    assistant_id: uuid.UUID | None = None,
) -> dict:
    """Return deduplicated erroneous tool-call rows for ontology editing."""
    since = datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 90)))
    items: list[dict] = []
    seen: set[tuple[str, str]] = set()

    if include_logs:
        rows = (await db.execute(
            select(LLMRequestLog)
            .where(
                LLMRequestLog.tenant_id == tenant_id,
                LLMRequestLog.created_at >= since,
                LLMRequestLog.debug.isnot(None),
            )
            .order_by(LLMRequestLog.created_at.desc())
            .limit(min(500, limit * 8))
        )).scalars().all()

        for log in rows:
            fc, meta = _classify_log_row(log)
            if not fc:
                continue
            query = _query_from_log(log)
            if len(query) < 6:
                continue
            key = (query.lower()[:200], fc)
            if key in seen:
                continue
            seen.add(key)
            items.append(_item_dict(
                item_id=f"log:{log.id}",
                source="log",
                query=query,
                failure_class=fc,
                expected_tool=meta.get("expected_tool"),
                called=meta.get("called") or [],
                semantic_top=meta.get("semantic_top") or [],
                tools_offered=meta.get("tools_offered") or [],
                created_at=log.created_at,
                log_id=str(log.id),
            ))
            if len(items) >= limit:
                break

    if include_audit_cases and len(items) < limit:
        q = (
            select(AssistantAuditCase, Assistant.name)
            .join(Assistant, Assistant.id == AssistantAuditCase.assistant_id)
            .where(
                Assistant.tenant_id == tenant_id,
                AssistantAuditCase.active.is_(True),
                AssistantAuditCase.last_result.isnot(None),
            )
            .order_by(AssistantAuditCase.updated_at.desc())
        )
        if assistant_id:
            q = q.where(AssistantAuditCase.assistant_id == assistant_id)
        audit_rows = (await db.execute(q.limit(limit * 2))).all()

        for case, asst_name in audit_rows:
            lr = case.last_result or {}
            if lr.get("passed"):
                continue
            called = lr.get("called") or []
            fc = _classify_audit_case(case.expected_tools, called)
            if fc == "pass":
                continue
            query = (case.question or "").strip()
            if len(query) < 4:
                continue
            key = (query.lower()[:200], fc)
            if key in seen:
                continue
            seen.add(key)
            expected = (case.expected_tools or [None])[0]
            items.append(_item_dict(
                item_id=f"audit:{case.id}",
                source="audit_case",
                query=query,
                failure_class=fc,
                expected_tool=expected.split("|")[0] if expected else None,
                called=called,
                semantic_top=[],
                tools_offered=[],
                created_at=case.updated_at or case.created_at,
                case_id=str(case.id),
                assistant_id=str(case.assistant_id),
                assistant_name=asst_name,
            ))
            if len(items) >= limit:
                break

    by_class: dict[str, int] = {}
    by_source: dict[str, int] = {}
    for it in items:
        by_class[it["failure_class"]] = by_class.get(it["failure_class"], 0) + 1
        by_source[it["source"]] = by_source.get(it["source"], 0) + 1

    return {
        "items": items,
        "summary": {
            "total": len(items),
            "by_failure_class": by_class,
            "by_source": by_source,
            "days": days,
            "since": since.isoformat(),
        },
    }
