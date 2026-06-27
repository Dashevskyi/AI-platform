"""LLM-assisted structured patches for ontology_json.

Read-only until the admin accepts patches in the UI — same pattern as tuner.diagnose().
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)

ALLOWED_OPS = {
    "merge_glossary",
    "merge_examples",
    "add_section",
    "append_freeform",
}

_SYSTEM = """Ты — редактор структурированной онтологии AI-ассистента.
Онтология — JSON с секциями: glossary, entities, relations, logic, examples, freeform.
Твоя задача — предложить ТОЧЕЧНЫЕ патчи (не переписывать всё целиком).

Доступные op (ТОЛЬКО из списка):
  merge_glossary  — добавить термины. data = {"items": [{"term": "...", "definition": "..."}]}
  merge_examples  — добавить примеры. data = {"items": [{"query": "...", "expected_tool": "...", "note": "..."}]}
  add_section     — новая секция. data = полный объект секции (type, title, items/entities/flows/text…)
  append_freeform — дописать текст. data = {"text": "..."} в существующую freeform-секцию (section_id)

Правила:
- Не удаляй и не затирай существующие секции целиком.
- expected_tool — только из списка доступных tools.
- 1–5 патчей, самые полезные.
- rationale — по-русски, кратко.

Верни СТРОГО JSON (без markdown):
{"summary": "<1 предложение>", "patches": [{"op": "...", "section_id": null, "section_type": "glossary", "title": null, "data": {}, "rationale": "..."}]}"""


def _extract_json_object(text: str) -> dict | None:
    if not text:
        return None
    s = text.strip()
    if "```" in s:
        s = s.replace("```json", "").replace("```", "")
    start = s.find("{")
    if start == -1:
        return None
    depth, in_str, esc = 0, False, False
    for j in range(start, len(s)):
        ch = s[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(s[start:j + 1])
                    return obj if isinstance(obj, dict) else None
                except json.JSONDecodeError:
                    return None
    return None


def _normalize_example_item(raw: dict) -> dict | None:
    if not isinstance(raw, dict):
        return None
    query = str(
        raw.get("query") or raw.get("question") or raw.get("user_query")
        or raw.get("request") or raw.get("text") or ""
    ).strip()
    if not query:
        return None
    return {
        "query": query,
        "expected_tool": str(
            raw.get("expected_tool") or raw.get("tool") or raw.get("tool_name") or raw.get("name") or ""
        ).strip(),
        "note": str(raw.get("note") or raw.get("comment") or "").strip(),
    }


def _normalize_example_items(items) -> list[dict]:
    if not isinstance(items, list):
        return []
    out = []
    for it in items:
        n = _normalize_example_item(it)
        if n:
            out.append(n)
    return out


def _normalize_patch(raw: dict, tool_names: set[str]) -> dict | None:
    op = str(raw.get("op") or "").strip()
    if op not in ALLOWED_OPS:
        return None
    data = raw.get("data")
    if not isinstance(data, dict) and op != "add_section":
        return None
    if op == "add_section":
        sec = raw.get("data") if isinstance(raw.get("data"), dict) else raw.get("section")
        if not isinstance(sec, dict) or not sec.get("type"):
            return None
        data = json.loads(json.dumps(sec))
        if data.get("type") == "examples" and isinstance(data.get("items"), list):
            data["items"] = _normalize_example_items(data["items"])
            if not data["items"]:
                return None
    if op in ("merge_glossary", "merge_examples"):
        items = (data or {}).get("items")
        if not isinstance(items, list) or not items:
            return None
        if op == "merge_examples":
            normalized = _normalize_example_items(items)
            if not normalized:
                return None
            data = {"items": normalized}
            for it in normalized:
                et = it.get("expected_tool") or ""
                if et and et not in tool_names:
                    it["expected_tool"] = ""
    return {
        "id": str(uuid.uuid4()),
        "op": op,
        "section_id": raw.get("section_id"),
        "section_type": raw.get("section_type"),
        "title": raw.get("title"),
        "data": data,
        "rationale": str(raw.get("rationale") or "")[:600],
    }


def build_user_prompt(
    *,
    task: str,
    ontology_json: dict | None,
    tools: list[dict],
    system_prompt: str | None,
    audit_cases: list[dict] | None,
) -> str:
    tool_lines = "\n".join(
        f"  - {t.get('name')}: {(t.get('description') or '')[:200]}"
        for t in tools[:80]
    ) or "  (нет tools)"
    audit_block = ""
    if audit_cases:
        lines = []
        for c in audit_cases[:12]:
            called = c.get("called") or []
            called_s = ", ".join(called) if isinstance(called, list) else str(called)
            lines.append(
                f"  Q: {c.get('question', '')[:200]} | expected: {c.get('expected_tool', '?')} | "
                f"called: {called_s or '—'} | fail: {c.get('failure_class', '?')}"
            )
        audit_block = "\n\nПРОВАЛЕННЫЕ КЕЙСЫ АУДИТА:\n" + "\n".join(lines)
    return f"""ЗАДАЧА: {task}

SYSTEM PROMPT (контекст ассистента):
{(system_prompt or '(не задан)')[:1500]}

ДОСТУПНЫЕ TOOLS:
{tool_lines}

ТЕКУЩАЯ ОНТОЛОГИЯ (ontology_json):
{json.dumps(ontology_json or {"version": 1, "sections": []}, ensure_ascii=False)[:12000]}
{audit_block}

Предложи JSON с patches."""


async def suggest_patches(
    provider,
    model_name: str,
    *,
    task: str,
    ontology_json: dict | None,
    tools: list[dict],
    system_prompt: str | None = None,
    audit_cases: list[dict] | None = None,
    max_tokens: int = 6000,
) -> dict:
    tool_names = {str(t.get("name") or "") for t in tools if t.get("name")}
    user = build_user_prompt(
        task=task,
        ontology_json=ontology_json,
        tools=tools,
        system_prompt=system_prompt,
        audit_cases=audit_cases,
    )
    try:
        resp = await provider.chat_completion(
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user},
            ],
            model=model_name,
            temperature=0.2,
            max_tokens=max_tokens,
            extra_body={"chat_template_kwargs": {"enable_thinking": False, "thinking": False}},
        )
    except Exception as e:
        logger.warning("ontology suggest LLM failed: %s", str(e)[:200])
        raise
    parsed = _extract_json_object(resp.content or "") or {}
    raw_patches = parsed.get("patches") if isinstance(parsed.get("patches"), list) else []
    patches = [p for p in (_normalize_patch(r, tool_names) for r in raw_patches if isinstance(r, dict)) if p]
    return {
        "summary": str(parsed.get("summary") or "")[:800],
        "patches": patches,
        "model": model_name,
    }


def apply_patches(ontology_json: dict | None, patches: list[dict]) -> dict:
    """Pure merge — caller persists. Applies only whitelisted ops."""
    base = json.loads(json.dumps(ontology_json or {"version": 1, "sections": []}))
    sections: list[dict] = list(base.get("sections") or [])

    def find_section(section_id: str | None, section_type: str | None) -> dict | None:
        if section_id:
            for s in sections:
                if str(s.get("id") or "") == str(section_id):
                    return s
        if section_type:
            for s in sections:
                if s.get("type") == section_type:
                    return s
        return None

    for patch in patches:
        op = patch.get("op")
        data = patch.get("data") or {}
        if op == "merge_glossary":
            sec = find_section(patch.get("section_id"), "glossary")
            if not sec:
                sec = {"id": f"n{uuid.uuid4().hex[:8]}", "type": "glossary", "title": "Глоссарий", "items": []}
                sections.append(sec)
            items = sec.setdefault("items", [])
            existing = {str(i.get("term", "")).lower() for i in items if isinstance(i, dict)}
            for it in data.get("items") or []:
                if not isinstance(it, dict):
                    continue
                term = str(it.get("term") or "").strip()
                if not term or term.lower() in existing:
                    continue
                existing.add(term.lower())
                items.append({"term": term, "definition": str(it.get("definition") or "").strip()})
        elif op == "merge_examples":
            sec = find_section(patch.get("section_id"), "examples")
            if not sec:
                sec = {"id": f"n{uuid.uuid4().hex[:8]}", "type": "examples", "title": "Примеры запросов", "items": []}
                sections.append(sec)
            items = sec.setdefault("items", [])
            seen = {str(i.get("query", "")).lower() for i in items if isinstance(i, dict)}
            for it in _normalize_example_items(data.get("items") or []):
                q = it.get("query", "").lower()
                if not q or q in seen:
                    continue
                seen.add(q)
                items.append(it)
        elif op == "add_section":
            sec = data if isinstance(data, dict) else {}
            if not sec.get("type"):
                continue
            sec = json.loads(json.dumps(sec))
            if sec.get("type") == "examples":
                incoming = _normalize_example_items(sec.get("items") or [])
                target = find_section(patch.get("section_id"), "examples")
                if not target:
                    target = {
                        "id": sec.get("id") or f"n{uuid.uuid4().hex[:8]}",
                        "type": "examples",
                        "title": sec.get("title") or "Примеры запросов",
                        "items": [],
                    }
                    sections.append(target)
                items = target.setdefault("items", [])
                seen = {str(i.get("query", "")).lower() for i in items if isinstance(i, dict)}
                for it in incoming:
                    q = it.get("query", "").lower()
                    if not q or q in seen:
                        continue
                    seen.add(q)
                    items.append(it)
            else:
                sec.setdefault("id", f"n{uuid.uuid4().hex[:8]}")
                sections.append(sec)
        elif op == "append_freeform":
            sec = find_section(patch.get("section_id"), "freeform")
            if not sec:
                sec = {"id": f"n{uuid.uuid4().hex[:8]}", "type": "freeform", "title": "Заметки", "text": ""}
                sections.append(sec)
            chunk = str(data.get("text") or "").strip()
            if chunk:
                prev = str(sec.get("text") or "").strip()
                sec["text"] = f"{prev}\n\n{chunk}".strip() if prev else chunk

    base["sections"] = sections
    base["version"] = 1
    return base
