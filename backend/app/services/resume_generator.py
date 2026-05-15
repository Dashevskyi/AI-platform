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


RESUME_PROMPT = """Опиши обмен «пользователь → ассистент» как краткий JSON-индекс темы.

ЦЕЛЬ: это резюме потом увидит сам ассистент, чтобы вспомнить ТЕМУ обмена. Конкретные значения (числа, IP, MAC, имена, идентификаторы, цены, версии, адреса, токены) НЕ ВКЛЮЧАТЬ — они извлекаются заново из артефактов и tool-результатов. Перепутанная цифра в резюме отравит будущие ответы.

ЗАПРЕЩЕНО в query и response:
- IP-адреса, маски, CIDR, MAC, порты, серийники
- Точные числа (цены, размеры, количество, версии, даты)
- Имена клиентов, ID документов, идентификаторы
- Кавычки с фрагментами кода или цитатами

РАЗРЕШЕНО в query и response:
- Тема обмена («пинг подсетей», «настройка PON-роутера», «парсинг счёта»)
- Категория действия ассистента («выдал скрипт», «запросил уточнение», «вызвал tool X», «прочитал документ»)
- Тип артефакта, если был («bash-скрипт», «SQL-запрос», «инструкция»)

Поля:
- query: 1 короткое предложение (до 20 слов) — о чём обмен.
- response: 1 короткое предложение (до 25 слов) — что СДЕЛАЛ ассистент. Без значений.

ПРИМЕРЫ (обрати внимание — никаких цифр/IP/имён, даже если они есть в исходном тексте):

вход пользователь: «добавь в скрипт 172.10.102.0/23»
ПЛОХО: query="добавить поддержку сети 172.10.102.0/23 в скрипт"
ХОРОШО: query="добавить ещё одну подсеть в существующий скрипт"

вход ассистент: «Вот SQL-запрос для получения 10 последних пользователей: SELECT id ...»
ПЛОХО: response="выдал SQL-запрос для выборки 10 пользователей с фильтром active=true"
ХОРОШО: response="выдал SQL-запрос для выборки активных пользователей"

вход пользователь: «какой роутер у клиента Касич?»
ПЛОХО: query="какой роутер у клиента Касич"
ХОРОШО: query="спросил о модели роутера у конкретного клиента"

Верни СТРОГО JSON: {{"query": "...", "response": "..."}}. Никаких комментариев, обёрток ```json, текста до/после.

Пользователь:
{user_content}

Ассистент:
{assistant_content}

JSON:"""


def _parse_resume_json(text: str) -> tuple[str | None, str | None]:
    """Strip code-fences if any, try json.loads, then regex fallback.
    Returns (query, response). Artifacts no longer flow through the resume —
    they are extracted into the artifacts table by the extractor, and the
    in-message `artifacts` JSONB is filled with references (ids), not data."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        obj = json.loads(cleaned)
        if isinstance(obj, dict):
            q = (obj.get("query") or "").strip() or None
            r = (obj.get("response") or "").strip() or None
            return q, r
    except json.JSONDecodeError:
        pass
    # Regex fallback — extract first two quoted strings after the keys.
    q = re.search(r'"query"\s*:\s*"([^"]+)"', cleaned)
    r = re.search(r'"response"\s*:\s*"([^"]+)"', cleaned)
    return (q.group(1).strip() if q else None), (r.group(1).strip() if r else None)


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
            # Snapshot the response_language while the cfg row is still attached
            # to a live session — the extractor below uses a different session.
            response_language = cfg.response_language
            prompt = RESUME_PROMPT.format(user_content=user_in, assistant_content=assistant_in)
            from app.services.llm.language import build_language_pin_message
            resp = await resolved.provider.chat_completion(
                messages=[
                    build_language_pin_message(response_language),
                    {"role": "user", "content": prompt},
                ],
                model=resolved.model_name,
                temperature=0.1,
                max_tokens=300,
            )

        query_resume, response_resume = _parse_resume_json(resp.content or "")
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
            # NOTE: assistant_msg.artifacts JSONB used to carry inline metadata;
            # now it holds only REFERENCES to rows in the `artifacts` table
            # (filled below after extract_and_save_artifacts). Resumes no longer
            # contain concrete values — see RESUME_PROMPT.
            assistant_msg.artifacts = None

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

            # Extract first-class artifacts from the assistant message (code
            # blocks, configs, scripts). Content goes into a dedicated table —
            # it's the immutable source of truth, no longer floating inside
            # the message.artifacts JSONB blob.
            try:
                from app.services.artifacts.extractor import extract_and_save_artifacts
                created = await extract_and_save_artifacts(
                    db=db,
                    tenant_id=tenant_id,
                    chat_id=assistant_msg.chat_id,
                    source_message_id=assistant_msg.id,
                    assistant_content=assistant_msg.content or "",
                    provider=resolved.provider,
                    model_name=resolved.model_name,
                    response_language=response_language,
                )
                # Backfill the JSONB column with REFERENCES (id + kind + label).
                # This lets HISTORY-RESUMES render `📎 [kind] label (id=...)`
                # without joining to the artifacts table — and crucially, the
                # data is not duplicated: `content` lives only in `artifacts`.
                if created:
                    assistant_msg.artifacts = [
                        {"id": str(a.id), "kind": a.kind, "label": a.label}
                        for a in created
                    ]
            except Exception:
                logger.exception(
                    "[resume] artifact extraction failed for pair user=%s asst=%s",
                    user_message_id, assistant_message_id,
                )

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
