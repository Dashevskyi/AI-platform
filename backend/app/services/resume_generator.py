"""Resume generator — compresses a (user_question, assistant_answer) pair into
1-2 sentence summaries and embeds them for semantic recall.

Triggered in the background after each successful assistant reply.
"""
from __future__ import annotations

import json
import logging
import re
import uuid

from sqlalchemy import select

from app.core.config import settings as app_settings
from app.core.database import async_session
from app.models.message import Message
from app.models.tenant_shell_config import TenantShellConfig
from app.providers.factory import get_provider
from app.services.llm.model_resolver import resolve_model
from app.services.memory.embedder import _resolve_embedding_model

logger = logging.getLogger(__name__)


RESUME_PROMPT = """Сожми обмен «пользователь → ассистент» в короткое JSON-резюме И выдели артефакты.

Артефакт = самостоятельный объект, который пользователь может захотеть позже изменять / переиспользовать:
- bash-script, python-script, sql-query, dockerfile, yaml-config, json-config, nginx-config
- code (любой код 5+ строк) — указать lang
- instruction (пошаговая инструкция / план), document (структурированный текст)

Если артефактов нет — верни artifacts: [].

Требования к полям:
- query: одно предложение (до 25 слов) — о чём СПРОСИЛ пользователь. Опусти приветствия.
- response: одно предложение (до 30 слов) — что СДЕЛАЛ/ОТВЕТИЛ ассистент. Если был результат от tool — ключевой факт (число, имя, статус), без полных списков.
- artifacts: массив объектов. Каждый: {{"kind": "<тип>", "label": "<краткое имя, до 8 слов>", "lang": "<bash|python|sql|yaml|...|null>"}}.
  label = ЧТО за артефакт (например "Скрипт пинга подсети 10.0.0.0/24"), не пересказ ответа.

Верни СТРОГО JSON: {{"query": "...", "response": "...", "artifacts": [...]}}. Никаких комментариев, обёрток ```json, текста до/после.

Пользователь:
{user_content}

Ассистент:
{assistant_content}

JSON:"""


_KNOWN_KINDS = {
    "bash-script", "python-script", "sql-query", "dockerfile",
    "yaml-config", "json-config", "nginx-config", "code",
    "instruction", "document",
}


def _normalize_artifacts(raw) -> list[dict]:
    """Sanitize the artifacts list from LLM output. Drops malformed entries."""
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw[:10]:  # hard cap — don't let the model spam
        if not isinstance(item, dict):
            continue
        kind = (str(item.get("kind") or "")).strip().lower()
        label = (str(item.get("label") or "")).strip()
        lang = item.get("lang")
        if lang is not None:
            lang = (str(lang)).strip().lower() or None
        if not kind or not label:
            continue
        if kind not in _KNOWN_KINDS:
            # Keep unknown kinds but normalize the slug.
            kind = re.sub(r"[^a-z0-9\-]+", "-", kind)[:40] or "code"
        out.append({"kind": kind, "label": label[:200], "lang": lang})
    return out


def _parse_resume_json(text: str) -> tuple[str | None, str | None, list[dict]]:
    """Strip code-fences if any, try json.loads, then regex fallback.
    Returns (query, response, artifacts)."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        obj = json.loads(cleaned)
        if isinstance(obj, dict):
            q = (obj.get("query") or "").strip() or None
            r = (obj.get("response") or "").strip() or None
            arts = _normalize_artifacts(obj.get("artifacts"))
            return q, r, arts
    except json.JSONDecodeError:
        pass
    # Regex fallback — extract first two quoted strings after the keys
    q = re.search(r'"query"\s*:\s*"([^"]+)"', cleaned)
    r = re.search(r'"response"\s*:\s*"([^"]+)"', cleaned)
    return (q.group(1).strip() if q else None), (r.group(1).strip() if r else None), []


async def generate_resume_for_pair(
    *,
    tenant_id: uuid.UUID,
    chat_id: uuid.UUID,
    user_message_id: uuid.UUID,
    assistant_message_id: uuid.UUID,
) -> None:
    """Generate resume_query and resume_response for the matching (user, assistant)
    pair, then embed and store. Best-effort: any failure is logged and swallowed."""
    try:
        async with async_session() as db:
            user_msg = (await db.execute(
                select(Message).where(Message.id == user_message_id)
            )).scalar_one_or_none()
            assistant_msg = (await db.execute(
                select(Message).where(Message.id == assistant_message_id)
            )).scalar_one_or_none()
            if not user_msg or not assistant_msg:
                return
            if user_msg.resume_query and assistant_msg.resume_response:
                return  # already done

            user_text = (user_msg.content or "").strip()
            assistant_text = (assistant_msg.content or "").strip()
            if not user_text or not assistant_text:
                return

            # Truncate inputs for the summary prompt — keep it cheap.
            max_chars = 4000
            user_in = user_text[:max_chars]
            assistant_in = assistant_text[:max_chars]

            cfg = (await db.execute(
                select(TenantShellConfig).where(TenantShellConfig.tenant_id == tenant_id)
            )).scalar_one_or_none()
            if not cfg:
                return

            # Use the same LLM the tenant talks to — we already have it warm.
            # Falls back to the configured shell-config provider via resolve_model.
            resolved = await resolve_model(str(tenant_id), user_in, db, cfg)
            prompt = RESUME_PROMPT.format(user_content=user_in, assistant_content=assistant_in)
            from app.services.llm.language import build_language_pin_message
            resp = await resolved.provider.chat_completion(
                messages=[
                    build_language_pin_message(cfg.response_language),
                    {"role": "user", "content": prompt},
                ],
                model=resolved.model_name,
                temperature=0.1,
                max_tokens=300,
            )

        query_resume, response_resume, artifacts = _parse_resume_json(resp.content or "")
        if not (query_resume or response_resume):
            logger.warning(
                "[resume] failed to parse JSON for msg pair user=%s asst=%s; raw=%r",
                user_message_id, assistant_message_id, (resp.content or "")[:200],
            )
            return

        # Save back + embed
        async with async_session() as db:
            user_msg = (await db.execute(
                select(Message).where(Message.id == user_message_id)
            )).scalar_one_or_none()
            assistant_msg = (await db.execute(
                select(Message).where(Message.id == assistant_message_id)
            )).scalar_one_or_none()
            if not user_msg or not assistant_msg:
                return

            user_msg.resume_query = query_resume
            assistant_msg.resume_response = response_resume
            # Artifacts are produced by the assistant — store on assistant row.
            # Empty list saved as NULL to keep the gin index lean.
            assistant_msg.artifacts = artifacts or None

            # Embed combined text — used by recall_chat for semantic lookup.
            combined = f"Q: {query_resume or ''}\nA: {response_resume or ''}".strip()
            embed_model = await _resolve_embedding_model(tenant_id, db)
            if embed_model and combined:
                try:
                    provider = get_provider(
                        "ollama",
                        app_settings.OLLAMA_BASE_URL or "http://localhost:11434",
                    )
                    vectors = await provider.embed(combined, embed_model)
                    if vectors:
                        # We store the embedding on the user message — that's the anchor for recall.
                        user_msg.resume_embedding = vectors[0]
                        user_msg.resume_embedding_model = embed_model
                except Exception:
                    logger.exception("[resume] embed failed for pair user=%s", user_message_id)

            await db.commit()
            logger.info(
                "[resume] saved for pair user=%s asst=%s (q=%d ch, r=%d ch)",
                user_message_id, assistant_message_id,
                len(query_resume or ""), len(response_resume or ""),
            )
    except Exception:
        logger.exception(
            "[resume] generation failed for pair user=%s asst=%s",
            user_message_id, assistant_message_id,
        )
