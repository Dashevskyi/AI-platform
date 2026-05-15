"""LLM-driven summary generator for attached files.

Uses a strict JSON-output protocol so the model can't drift into another
language or freeform reasoning. Same shape and style as resume_generator.py,
which is the proven pattern in this codebase.
"""
from __future__ import annotations

import json
import logging
import re

from app.services.llm.language import build_language_pin_message, language_name

logger = logging.getLogger(__name__)


_SUMMARY_PROMPT = """Прочитай содержимое прикреплённого документа и верни СТРОГО JSON.

Требования к полю summary:
- 2-3 предложения {language_human}.
- Конкретно: модели оборудования, числа, ключевые сущности, ключевые слова.
- Если есть итоговая цена/сумма/количество — упомяни.
- Если документ это скрипт / конфиг / SQL / инструкция — назови назначение.
- Без markdown-разметки, без вводных «вот», «извините», «как видно».

Верни СТРОГО JSON, без обёрток ```json, без текста до или после:
{{"summary": "..."}}

СОДЕРЖИМОЕ ДОКУМЕНТА:
{content}

JSON:"""


def _parse_summary_json(text: str) -> str | None:
    """Strip code fences if any, try json.loads, then regex fallback."""
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        obj = json.loads(cleaned)
        if isinstance(obj, dict):
            value = obj.get("summary")
            if isinstance(value, str):
                return value.strip() or None
    except json.JSONDecodeError:
        pass
    m = re.search(r'"summary"\s*:\s*"((?:[^"\\]|\\.)*)"', cleaned)
    if m:
        return m.group(1).strip() or None
    return None


async def generate_attachment_summary(
    *,
    content: str,
    provider,
    model_name: str,
    language: str | None,
) -> str:
    """Run the LLM-summary call and return the parsed text. Empty string on
    failure — caller decides on fallback."""
    prompt = _SUMMARY_PROMPT.format(
        language_human=f"на языке: {language_name(language)}",
        content=content,
    )
    resp = await provider.chat_completion(
        messages=[
            build_language_pin_message(language),
            {"role": "user", "content": prompt},
        ],
        model=model_name,
        temperature=0.1,
        max_tokens=350,
    )
    raw = (resp.content or "").strip()
    parsed = _parse_summary_json(raw)
    if parsed:
        return parsed[:500]
    logger.warning("[attachment-summary] failed to parse JSON, raw=%r", raw[:200])
    # Last resort: return the raw text trimmed — better than nothing.
    return raw[:500]
