import asyncio
import json
import logging
import os
import re
import time
import uuid

try:
    import tiktoken
    _TIKTOKEN_ENC = tiktoken.get_encoding("cl100k_base")
except Exception:  # pragma: no cover
    _TIKTOKEN_ENC = None


def _ct(text: str | None) -> int:
    """Approximate token count via tiktoken cl100k_base.
    Used for per-section breakdown (telemetry only — exact tokens
    come from the provider in usage.prompt_tokens)."""
    if not text:
        return 0
    if _TIKTOKEN_ENC is not None:
        try:
            return len(_TIKTOKEN_ENC.encode(text, disallowed_special=()))
        except Exception:
            pass
    return max(1, len(text) // 4)  # crude fallback


def _ct_obj(obj) -> int:
    if obj is None:
        return 0
    try:
        return _ct(json.dumps(obj, ensure_ascii=False))
    except Exception:
        return 0


def _detect_user_language(text: str) -> str | None:
    """Best-effort language hint for tool-result reminders.
    Qwen3 (and Qwen2.5 to a degree) tends to switch to the language of tool
    results (often English JSON). Returns Russian/Ukrainian/None — English does
    not need a reminder."""
    if not text:
        return None
    cyrillic = sum(1 for ch in text if "Ѐ" <= ch <= "ӿ")
    ascii_alpha = sum(1 for ch in text if ch.isalpha() and ord(ch) < 128)
    total = cyrillic + ascii_alpha
    if total < 10:
        return None
    if cyrillic / total > 0.5:
        if any(ch in text for ch in "іїєґІЇЄҐ"):
            return "украинском"
        return "русском"
    return None


# Distinctive tokens worth pinning: long numbers (≥4 digits), MAC, IPv4, IPv6 fragments,
# ALL-CAPS tags (ABC-123), UUID-like, long identifiers. These typically appear when
# the model "uses" a value from a tool result in its next reasoning/tool_call.
_DISTINCTIVE_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:"
    r"[0-9]{4,}|"
    r"[A-Z]{2,5}-[0-9]+|"
    r"[0-9a-fA-F]{2}[:\-][0-9a-fA-F]{2}[:\-][0-9a-fA-F]{2}[:\-][0-9a-fA-F]{2}[:\-][0-9a-fA-F]{2}[:\-][0-9a-fA-F]{2}|"
    r"[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}|"
    r"[A-Za-z][A-Za-z0-9_]{8,}"
    r")(?![A-Za-z0-9_])"
)


def _extract_distinctive_tokens(text: str) -> set[str]:
    if not text:
        return set()
    return set(_DISTINCTIVE_TOKEN_RE.findall(text)[:80])  # cap to keep lookup cheap


def _is_referenced_in(tokens: set[str], blob: str) -> bool:
    if not tokens or not blob:
        return False
    for tok in tokens:
        if tok in blob:
            return True
    return False


def _deterministic_compress(content: str, keep_chars: int | None = None) -> str:
    """Cheap structural compression: keep head + tail, drop middle, leave marker."""
    if keep_chars is None:
        keep_chars = TOOL_RESULT_DETERMINISTIC_KEEP  # late binding (constant declared later in module)
    if len(content) <= keep_chars:
        return content
    head_len = int(keep_chars * 0.65)
    tail_len = int(keep_chars * 0.30)
    head = content[:head_len]
    tail = content[-tail_len:]
    omitted = len(content) - head_len - tail_len
    return f"{head}\n\n... [{omitted} символов сжато — полные данные через повторный вызов tool] ...\n\n{tail}"


def _with_lang_reminder(content: str, user_content: str) -> str:
    """Append a language reminder to tool output if user wrote in non-English.
    Cheap fix for Qwen3 reasoning drifting to English after a tool turn."""
    lang = _detect_user_language(user_content)
    if not lang:
        return content
    return content + f"\n\n---\n[Напоминание: продолжай рассуждения и итоговый ответ на {lang} языке.]"


def _with_language_system_tail(messages: list[dict], user_content: str) -> list[dict]:
    """Append a final system-role reminder so it's the LAST thing the model sees
    before generating. Qwen3 chat template gives the last system message strong
    weight inside <think>. Returns NEW list (does not mutate caller's)."""
    lang = _detect_user_language(user_content)
    if not lang:
        return messages
    tail = {
        "role": "system",
        "content": (
            f"⚠ Reasoning policy (FINAL): "
            f"chain-of-thought (<think>) AND the final reply MUST be in {lang} языке. "
            f"NOT in English. Tool outputs may be English JSON — translate them when needed, "
            f"but never switch your own reasoning to English."
        ),
    }
    return messages + [tail]


# Lazy-intent detection: model wrote "I'll do X" or "wait while I check" but did not call any tool.
_LAZY_INTENT_RE = re.compile(
    r"\b(?:"
    r"вызываю|вызов[у]?|сейчас\s+(?:вызову|выполню|проверю|запрошу|найду|посмотрю)|"
    r"выполня[юе]т?|выполн[юу]|проверя[юе]|проверю|запраш[ия]ваю|запрошу|"
    r"ищу|поищу|найду|посмотрю|подожди[а-яё]*|подождите|"
    r"секунд[уыа]?|3[\s-]*5\s*секунд|пару\s+секунд|"
    r"i['’]?ll\s+(?:check|search|call|find|look|verify)|"
    r"let\s+me\s+(?:check|search|call|find|look|verify)|please\s+wait"
    r")\b",
    re.IGNORECASE,
)


def _is_lazy_response(content: str) -> bool:
    if not content:
        return False
    return bool(_LAZY_INTENT_RE.search(content))


def _resolve_thinking_kwargs(
    mode: str | None,
    user_content: str,
    has_tools: bool,
) -> dict | None:
    """Build extra_body for vLLM chat_template_kwargs.enable_thinking.
    Only models that honor this flag (Qwen3, DeepSeek-R1) react;
    others (Qwen2.5, Llama, Mistral) silently ignore it."""
    m = (mode or "on").lower()
    if m == "off":
        return {"chat_template_kwargs": {"enable_thinking": False}}
    if m == "auto":
        is_short = len((user_content or "").strip()) < 100
        if is_short and not has_tools:
            return {"chat_template_kwargs": {"enable_thinking": False}}
    # "on" or auto-needs-thinking → no override (model's default behavior)
    return None

from sqlalchemy import select
from sqlalchemy import func as sa_func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AdminAuditLog,
    Chat,
    KnowledgeBaseDocument,
    KBChunk,
    LLMRequestLog,
    MemoryEntry,
    Message,
    MessageAttachment,
    TenantApiKey,
    TenantApiKeyGroup,
    TenantShellConfig,
    TenantTool,
)
from app.providers.factory import get_provider
from app.core.security import decrypt_value, redact_for_log
from app.services.tools.executor import execute_tool
from app.services.kb.embedder import search_kb_chunks
from app.services.llm.model_resolver import resolve_model
from app.services.llm.context_compressor import RECENT_MESSAGES_FULL, trim_tool_definitions
from app.services.throttle import get_or_create_throttle, ThrottleRejected
from app.models.tenant import Tenant

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 6  # prevent infinite tool-call loops
# Anti-lazy auto-nudge — was needed for Qwen3 thinking-mode quirk. For DeepSeek and
# Qwen2.5 (no <think> block) it can actually cause runaway: model with no clear
# next action ends with content ("сделано"), regex matches "сделано/проверю/etc",
# nudge fires → more tool spam. Off by default. Re-enable via env if back on Qwen3.
ANTI_LAZY_ENABLED = (os.getenv("ANTI_LAZY_ENABLED") or "0").lower() in ("1", "true", "yes")
# Auto-extract memory from each turn (background mini-LLM pass). Off by default —
# was creating too much client-data noise. Explicit memory_save tool is preferred.
MEMORY_AUTO_EXTRACT = (os.getenv("MEMORY_AUTO_EXTRACT") or "0").lower() in ("1", "true", "yes")
# Tool results longer than this will be summarized in subsequent rounds
# Compression thresholds for old tool results (data in messages from previous rounds).
TOOL_RESULT_COMPRESS_THRESHOLD = 800       # below this — never touch
TOOL_RESULT_DETERMINISTIC_KEEP = 1200      # target size after deterministic compress
TOOL_RESULT_LLM_SUMMARY_AT = 10000         # above this — LLM-summarize unpinned
TOOL_RESULT_SUMMARIZE_THRESHOLD = TOOL_RESULT_COMPRESS_THRESHOLD  # legacy alias
MAX_SAFE_TEMPERATURE = 0.7
VALID_CONTEXT_MODES = {"recent_only", "summary_plus_recent", "summary_only"}


EventEmitter = "Callable[[str, dict], Awaitable[None]] | None"


def _usage_totals(resp) -> tuple[int, int]:
    prompt_tokens = getattr(resp, "prompt_tokens", None) or 0
    completion_tokens = getattr(resp, "completion_tokens", None) or 0
    return int(prompt_tokens), int(completion_tokens)


async def chat_completion(
    tenant_id: str,
    chat_id: str,
    user_content: str,
    db: AsyncSession,
    user_message_id: str | None = None,
    api_key_id: str | None = None,
    on_event=None,
    merged_message_ids: list[str] | None = None,
) -> dict:
    """Public entrypoint: applies tenant throttle then runs pipeline."""
    tenant = None
    try:
        tenant = (
            await db.execute(
                select(Tenant).where(Tenant.id == uuid.UUID(str(tenant_id)), Tenant.deleted_at.is_(None))
            )
        ).scalar_one_or_none()
    except Exception:
        tenant = None

    if tenant and tenant.throttle_enabled:
        throttle = await get_or_create_throttle(
            tenant_id,
            max_concurrent=tenant.throttle_max_concurrent,
            max_queue=tenant.throttle_queue_max,
            overflow_policy=tenant.throttle_overflow_policy,
        )
        async with throttle.slot():
            return await _chat_completion_inner(
                tenant_id, chat_id, user_content, db, user_message_id, api_key_id, on_event,
                merged_message_ids,
            )
    return await _chat_completion_inner(
        tenant_id, chat_id, user_content, db, user_message_id, api_key_id, on_event,
        merged_message_ids,
    )


async def _chat_completion_inner(
    tenant_id: str,
    chat_id: str,
    user_content: str,
    db: AsyncSession,
    user_message_id: str | None = None,
    api_key_id: str | None = None,
    on_event=None,
    merged_message_ids: list[str] | None = None,
) -> dict:
    """
    Full LLM pipeline with tool execution support:
    1. Load shell config
    2. Load recent messages
    3. Load memory/KB/tools
    4. Build messages array
    5. Call provider
    6. If tool_calls → execute tools → feed results back → call provider again (up to MAX_TOOL_ROUNDS)
    7. Save LLM request log
    8. Auto-summary
    9. Return response
    """
    correlation_id = str(uuid.uuid4())

    async def _emit(event_type: str, payload: dict) -> None:
        if on_event is None:
            return
        try:
            await on_event(event_type, {"correlation_id": correlation_id, **payload})
        except Exception:
            logger.warning(f"[{correlation_id}] on_event raised; ignoring", exc_info=True)

    await _emit("pipeline_start", {"chat_id": chat_id})

    # 1. Load config
    config = (
        await db.execute(
            select(TenantShellConfig).where(TenantShellConfig.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if not config:
        raise ValueError("Shell config not found for tenant")

    # 2. Load recent messages (exclude error messages)
    msg_q = (
        select(Message)
        .where(
            Message.chat_id == chat_id,
            Message.tenant_id == tenant_id,
            Message.status != "error",
            ~Message.content.like("Ошибка:%"),
        )
        .order_by(Message.created_at.desc())
        .limit(config.max_context_messages)
    )
    recent_msgs = list(reversed((await db.execute(msg_q)).scalars().all()))
    total_messages_count = (await db.execute(
        select(sa_func.count()).select_from(Message).where(
            Message.chat_id == chat_id,
            Message.tenant_id == tenant_id,
        )
    )).scalar() or 0

    # Exclude current user message from history (it will be appended explicitly)
    exclude_ids: set[str] = set()
    if user_message_id:
        exclude_ids.add(str(user_message_id))
    if merged_message_ids:
        exclude_ids.update(str(x) for x in merged_message_ids)
    if exclude_ids and recent_msgs:
        recent_msgs = [m for m in recent_msgs if str(m.id) not in exclude_ids]

    # 3. Memory — pinned entries always included; the rest selected by
    #    semantic similarity to the user's current message.
    memory_entries: list = []
    if config.memory_enabled:
        # Always-on: pinned entries + scope (this chat or tenant-wide)
        pinned_q = (
            select(MemoryEntry)
            .where(
                MemoryEntry.tenant_id == tenant_id,
                MemoryEntry.deleted_at.is_(None),
                MemoryEntry.is_pinned.is_(True),
                (MemoryEntry.chat_id == chat_id) | (MemoryEntry.chat_id.is_(None)),
            )
            .order_by(MemoryEntry.priority.desc())
        )
        pinned_entries = list((await db.execute(pinned_q)).scalars().all())
        # Semantic top-N via embeddings — ignored gracefully if no embedding model configured
        semantic_entries: list = []
        if config.embedding_model_name:
            try:
                from app.services.memory.embedder import search_memory_entries
                semantic_entries = list(await search_memory_entries(
                    tenant_id=str(tenant_id),
                    chat_id=str(chat_id),
                    query=user_content,
                    db=db,
                    embedding_model=config.embedding_model_name,
                    top_k=8,
                ))
            except Exception:
                logger.exception(f"[{correlation_id}] memory semantic search failed; falling back to priority-only")
        if not semantic_entries:
            # Fallback: top-N by priority among non-pinned (preserves old behaviour
            # if embeddings aren't ready yet — backfill is async)
            fallback_q = (
                select(MemoryEntry)
                .where(
                    MemoryEntry.tenant_id == tenant_id,
                    MemoryEntry.deleted_at.is_(None),
                    MemoryEntry.is_pinned.is_(False),
                    (MemoryEntry.chat_id == chat_id) | (MemoryEntry.chat_id.is_(None)),
                )
                .order_by(MemoryEntry.priority.desc(), MemoryEntry.created_at.desc())
                .limit(8)
            )
            semantic_entries = list((await db.execute(fallback_q)).scalars().all())
        # De-dup
        seen_ids = set()
        memory_entries = []
        for m in [*pinned_entries, *semantic_entries]:
            if m.id in seen_ids:
                continue
            seen_ids.add(m.id)
            memory_entries.append(m)

    # 4. Resolve model via catalog (or fallback to shell_config)
    resolved = await resolve_model(tenant_id, user_content, db, config)
    provider = resolved.provider
    model_name = resolved.model_name
    effective_temperature = _clamp_temperature(config.temperature)
    logger.debug(f"[{correlation_id}] Model resolved: {model_name} (source={resolved.source}, provider={resolved.provider_type})")

    # 5. KB — semantic search via embeddings (skip if no KB documents exist)
    kb_chunks: list = []
    if config.knowledge_base_enabled and config.embedding_model_name:
        # Quick check: do any KB chunks exist for this tenant?
        kb_exists = (await db.execute(
            select(sa_func.count()).select_from(
                select(KBChunk.id).where(KBChunk.tenant_id == tenant_id).limit(1).subquery()
            )
        )).scalar()
        if kb_exists:
            await _emit("kb_search_start", {"query": user_content[:120]})
            try:
                from app.core.config import settings
                embed_provider = get_provider("ollama", settings.OLLAMA_BASE_URL or "http://localhost:11434")
                kb_chunks = await search_kb_chunks(
                    tenant_id=tenant_id,
                    query=user_content,
                    db=db,
                    provider=embed_provider,
                    embedding_model=config.embedding_model_name,
                    max_results=config.kb_max_chunks or 10,
                )
            except Exception as e:
                logger.warning(f"KB semantic search failed: {e}")
            await _emit("kb_search_done", {"chunks_count": len(kb_chunks)})

    # 6. Load processed chat attachments first — they inform tool routing.
    attachment_tool_defs: list[dict] = []
    attachment_map: dict[str, str] = {}  # tool_name -> attachment_id
    attachments_q = select(MessageAttachment).where(
        MessageAttachment.chat_id == chat_id,
        MessageAttachment.tenant_id == tenant_id,
        MessageAttachment.processing_status == "done",
    )
    chat_attachments = list((await db.execute(attachments_q)).scalars().all())

    # Split into attachments attached to THIS user message (the freshest, must
    # get prime placement next to the question) vs everything attached earlier
    # in this chat (background context — listed in system block).
    current_message_attachments: list[MessageAttachment] = []
    previous_chat_attachments: list[MessageAttachment] = []
    if user_message_id:
        try:
            cur_uuid = uuid.UUID(str(user_message_id))
        except (ValueError, TypeError):
            cur_uuid = None
        for att in chat_attachments:
            if cur_uuid is not None and att.message_id == cur_uuid:
                current_message_attachments.append(att)
            else:
                previous_chat_attachments.append(att)
    else:
        previous_chat_attachments = chat_attachments

    needs_tools = _query_needs_tools(user_content, chat_attachments)
    tool_route = _detect_tool_route(user_content)
    # Resolve API-key tool access early — empty allowed_tool_ids means key has no tool access
    allowed_tool_ids = await _load_allowed_tool_ids(db, tenant_id, api_key_id)
    key_blocks_tools = allowed_tool_ids is not None and len(allowed_tool_ids) == 0

    if config.tools_policy == "never":
        tools_enabled = False
    elif config.tools_policy == "always":
        tools_enabled = resolved.supports_tools and not key_blocks_tools
    else:
        tools_enabled = needs_tools and resolved.supports_tools and not key_blocks_tools

    if needs_tools and not resolved.supports_tools:
        logger.debug(f"[{correlation_id}] Skipping tools — selected model does not support tool calling")
    if needs_tools and key_blocks_tools:
        logger.debug(f"[{correlation_id}] Skipping tools — API key has no allowed tools")

    tools: list = []
    tool_config_map: dict[str, dict] = {}
    if tools_enabled:
        tools_q = select(TenantTool).where(
            TenantTool.tenant_id == tenant_id,
            TenantTool.is_active == True,  # noqa: E712
            TenantTool.deleted_at.is_(None),
        )
        all_tools = list((await db.execute(tools_q)).scalars().all())
        if allowed_tool_ids is not None:
            before_count = len(all_tools)
            all_tools = [tool for tool in all_tools if str(tool.id) in allowed_tool_ids]
            logger.debug(
                f"[{correlation_id}] Tool access filter: {len(all_tools)}/{before_count} tools allowed"
            )
        tools = await _select_relevant_tools(
            all_tools, user_content, provider, model_name,
            embedding_model=config.embedding_model_name,
            db=db,
            tenant_id=str(tenant_id),
        )
        tool_config_map = {
            t.config_json["function"]["name"]: t.config_json
            for t in tools
            if isinstance(t.config_json, dict)
            and isinstance(t.config_json.get("function"), dict)
            and t.config_json["function"].get("name")
        }
        # Builtin tools — the system retrieval/memory/artifacts toolset.
        # Lives in code (app/services/tools/builtin_registry.py), not in
        # tenant_tools. Always added on top of whatever tenant tools the
        # semantic selector chose; new tenants get them automatically.
        from app.services.tools.builtin_registry import builtin_tool_config_map
        for _bt_name, _bt_cfg in builtin_tool_config_map().items():
            # Per-request copy — runtime context is injected below and we
            # don't want to mutate the registry's singleton dicts.
            tool_config_map[_bt_name] = dict(_bt_cfg)
        # Inject runtime context (tenant_id, chat_id) into each tool_config so
        # built-in handlers like memory_save can write to the right tenant/chat.
        # Copy is shallow but acceptable — handlers only read _context.
        for _name, _cfg in tool_config_map.items():
            _cfg["_context"] = {
                "tenant_id": str(tenant_id),
                "chat_id": str(chat_id),
                "api_key_id": str(api_key_id) if api_key_id else None,
            }

        # For strongly tool-driven PON workflows, reduce non-essential context
        # so local models focus on current entities instead of old turns.
        if tool_route == TOOL_ROUTE_PON:
            memory_entries = [m for m in memory_entries if getattr(m, "is_pinned", False)]
            kb_chunks = []

        if chat_attachments:
            from app.services.attachments.tool import build_attachment_tool_def
            for att in chat_attachments:
                tool_def = build_attachment_tool_def(str(att.id), att.filename, att.summary)
                attachment_tool_defs.append(tool_def)
                tool_name = tool_def["function"]["name"]
                attachment_map[tool_name] = str(att.id)
    # `allowed_tool_names` should be ALL tools the tenant has permission to call,
    # not only those we semantically selected for the payload. Semantic selection
    # decides what's *shown* to the model (token budget), not what's *executable*.
    # The model may know about other tools from KB / memory / history / ontology
    # and should be able to call them — we'll execute them and (on the next round)
    # ensure they make it into the payload.
    all_allowed_tools_for_tenant: dict[str, dict] = {}
    if tools_enabled:
        # Build the full allow-set from the same TenantTool fetch (all_tools) we already loaded.
        for t in all_tools:
            if not isinstance(t.config_json, dict):
                continue
            fn = t.config_json.get("function") or {}
            n = fn.get("name") if isinstance(fn, dict) else None
            if n:
                all_allowed_tools_for_tenant[n] = t.config_json
        # Builtin tools — system retrieval/memory/artifacts. Always callable.
        from app.services.tools.builtin_registry import builtin_tool_config_map
        for _bt_name, _bt_cfg in builtin_tool_config_map().items():
            all_allowed_tools_for_tenant[_bt_name] = dict(_bt_cfg)
    allowed_tool_names = (
        set(tool_config_map.keys())
        | set(attachment_map.keys())
        | set(all_allowed_tools_for_tenant.keys())
    )
    # Inject runtime context into the full allow-set too (needed for memory_save etc.)
    for _name, _cfg in all_allowed_tools_for_tenant.items():
        _cfg["_context"] = {
            "tenant_id": str(tenant_id),
            "chat_id": str(chat_id),
            "api_key_id": str(api_key_id) if api_key_id else None,
        }

    # 7. Build messages
    #
    # ============================================================================
    # MINIMAL PROMPT MODE
    # ----------------------------------------------------------------------------
    # Все hardcoded инжекты в system временно ЗАКОММЕНТИРОВАНЫ. В контекст идёт
    # только то, что админ tenant'a явно прописал в shell_config.system_prompt
    # и shell_config.rules_text. По мере того как мы будем точно понимать что
    # модели реально нужно и как это работает — будем раскомментировать по одному
    # блоку (anti-lazy → markdown → economy → memory → KB → attachments → ...).
    #
    # Любой блок ниже легко вернуть: убрать `if False:` и проверить эффект на
    # тестовой выборке запросов. См. соответствующий tokens_* в Logs Tab.
    # ============================================================================
    system_parts: list[str] = []
    # Language pin — first system part so the lock is the very first thing the
    # model sees. Tenant chooses the language in shell config (default 'ru').
    from app.services.llm.language import build_language_pin_text
    system_parts.append(build_language_pin_text(getattr(config, "response_language", "ru")))
    if config.system_prompt:
        system_parts.append(config.system_prompt)
    if getattr(config, "ontology_prompt", None) and config.ontology_prompt.strip():
        system_parts.append(config.ontology_prompt.strip())
    if config.rules_text:
        system_parts.append(f"Rules:\n{config.rules_text}")

    if False:  # === [HARDCODED-1] language hint ===
        system_parts.append(
            "Отвечай на том же языке, на котором обращается пользователь "
            "(русский → русский, украинский → украинский, английский → английский). "
            "Технические термины (IP, MAC, DHCP, VLAN, BGP) оставляй как есть."
        )

    if True:  # === [HARDCODED-2] anti-hallucination ===
        system_parts.append(
            "## Источники истины\n"
            "Конкретные значения (IP, MAC, числа, имена, идентификаторы, версии, цены, "
            "коды ошибок, фрагменты кода) бери ТОЛЬКО из явных источников:\n"
            "- результат вызова tool в этом ответе;\n"
            "- блок «Активные артефакты» в текущем сообщении (artifacts.content);\n"
            "- блок «Knowledge Base» в system;\n"
            "- блок «Закреплённая память»;\n"
            "- содержимое прикреплённого к этому сообщению файла.\n\n"
            "Если значения нет ни в одном из этих источников — НЕ выдумывай. "
            "Скажи прямо: «у меня нет данных», «не вижу этого в источниках», "
            "«нужно вызвать tool/посмотреть документ». Допустимо предложить "
            "способ узнать (какой tool вызвать, какой документ открыть).\n\n"
            "Резюме в Recent conversation НЕ источник конкретных значений — оно "
            "содержит только тему обмена. За конкретикой иди в артефакт / память / "
            "KB / get_message."
        )

    if True:  # === [HARDCODED-3] anti-lazy — "не описывай, делай" ===
        system_parts.append(
            "## Действие вместо описания\n"
            "Если для ответа нужен факт из системы — СРАЗУ вызывай tool, без "
            "предисловий «сейчас проверю / выполню / запрошу». Описание "
            "намерения без сопровождающего tool_call = пустой ответ.\n"
            "После неудачного результата tool (ошибка/пусто) — не пиши «попробую "
            "другой способ», а сразу делай следующий вызов. Цепочка 2-3 tool_calls "
            "подряд — норма, если задача требует."
        )

    if True:  # === [HARDCODED-4] markdown tables for structured data ===
        system_parts.append(
            "## Формат ответа\n"
            "Однотипные записи и сравнения — компактной markdown-таблицей "
            "(колонки через `|`), не сплошным текстом. CSV не используй "
            "в ответах чата — он только для экспорта."
        )

    if True:  # === [HARDCODED-5] tool context economy ===
        system_parts.append(
            "## Экономия контекста при вызове tools\n"
            "Для tools которые могут вернуть много (логи, leases, history, дерево "
            "топологии, события DHCP, заявки) — обязательно `limit` (20-50) "
            "и/или фильтр (адрес/клиент/дата/severity). «Все логи» / «всё "
            "дерево» без причины — нет.\n"
            "Между раундами старые tool-результаты автоматически сжимаются: "
            "полностью сохраняются только значения, которые ты упомянул в "
            "ответе или последующих вызовах. Если полный результат снова "
            "нужен — повтори tool с теми же аргументами."
        )

    if True:  # === [HARDCODED-6] tool routing hint ===
        # Active only when a domain route is detected (e.g. PON keywords in
        # the user query) — emits a stepwise hint telling the model the
        # correct call order for that domain's tools.
        route_hint = _tool_route_system_hint(tool_route, allowed_tool_names)
        if route_hint:
            system_parts.append("## " + route_hint)

    # Collect long_term memory items from API key and its group (scoped to chats with this key)
    api_key_memory_items: list[str] = []
    if True:  # === [BLOCK-MEMORY-A] memory_prompt from api_key / group ===
        if api_key_id:
            api_key = (
                await db.execute(
                    select(TenantApiKey).where(
                        TenantApiKey.id == api_key_id,
                        TenantApiKey.tenant_id == tenant_id,
                    )
                )
            ).scalar_one_or_none()
            if api_key and api_key.memory_prompt and api_key.memory_prompt.strip():
                api_key_memory_items.append(api_key.memory_prompt.strip())
            if api_key and api_key.group_id:
                group = (
                    await db.execute(
                        select(TenantApiKeyGroup).where(
                            TenantApiKeyGroup.id == api_key.group_id,
                            TenantApiKeyGroup.tenant_id == tenant_id,
                        )
                    )
                ).scalar_one_or_none()
                if group and group.memory_prompt and group.memory_prompt.strip():
                    api_key_memory_items.append(group.memory_prompt.strip())
        # Inject as its own system block — these are explicit tenant configs
        # for the active API key / group, not LLM-saved memory.
        if api_key_memory_items:
            system_parts.append(
                "## Память API-ключа\n"
                + "\n".join(f"- {item}" for item in api_key_memory_items)
            )

    _memory_block_text: str | None = None
    _kb_block_text: str | None = None
    _attachments_block_text: str | None = None
    if True:  # === [BLOCK-MEMORY-B] PINNED memory entries from DB ===
        # Only pinned entries land here — they're explicit "always remember
        # this" facts. Non-pinned entries stay out of the system prompt and
        # are reachable on-demand via the `recall_memory` tool (semantic
        # search). This keeps the system block from ballooning as memory
        # grows, and avoids the self-poisoning risk where every save_memory
        # entry permanently lives in attention.
        pinned_only = [m for m in memory_entries if getattr(m, "is_pinned", False)]
        if pinned_only:
            mem_lines = [f"- [{m.memory_type}] {m.content}" for m in pinned_only]
            _memory_block_text = (
                "## Закреплённая память (always-on facts)\n"
                + "\n".join(mem_lines)
                + "\n\nДля поиска по остальной памяти — вызови tool `recall_memory(query=...)`."
            )
            system_parts.append(_memory_block_text)

    if True:  # === [BLOCK-KB] knowledge base excerpts ===
        # Semantic top-K KB chunks for the current user message. These are
        # background domain knowledge — not the user's artifacts. Stays in
        # `system` (not user-message) because it's reference material, not
        # something we expect the model to edit or treat as the subject.
        # Empty/low-quality result → nothing emitted; modèle can fall back to
        # the `search_kb` tool for a wider query.
        if kb_chunks:
            kb_parts = []
            for c in kb_chunks:
                entry = f"[{c.doc_title}]"
                if c.source_type and c.source_type != "manual":
                    entry += f" ({c.source_type})"
                if c.source_url:
                    entry += f" src: {c.source_url}"
                entry += f"\n{c.content}"
                kb_parts.append(entry)
            _kb_block_text = (
                "## Knowledge Base (релевантные выдержки)\n"
                + "\n---\n".join(kb_parts)
                + "\n\nЭто справочные материалы. Если нужного нет — вызови `search_kb(query=...)` "
                + "с другой формулировкой."
            )
            system_parts.append(_kb_block_text)

    if True:  # === [BLOCK-ATTACHMENTS] previously-attached files in this chat ===
        # Files attached to OLDER messages — background context. Files attached
        # to THIS user message are inlined next to the question (see below).
        if previous_chat_attachments:
            att_lines = []
            for att in previous_chat_attachments:
                att_lines.append(
                    f"- {att.filename} ({att.file_type}, {att.file_size_bytes} байт): "
                    f"{att.summary or 'нет описания'}"
                )
            header = "Файлы чата (приложены ранее):\n"
            if tools_enabled:
                header = (
                    "Файлы чата (приложены ранее, не относятся к текущему сообщению — "
                    "это контекст. Для поиска внутри используй search_attachment_*):\n"
                )
            _attachments_block_text = header + "\n".join(att_lines)
            system_parts.append(_attachments_block_text)

    # === [BLOCK-ACTIVE-ARTIFACTS] — auto-grounding ===
    # Pull the artifacts the user's question is most likely about (semantic
    # match + recency hot-set). VERBATIM content is later inlined directly
    # INTO the user message (not system) so the model attends to it the same
    # way it attends to the question itself — matches how Cursor/ChatGPT
    # present open files. Block payload is built here, attached below.
    active_artifacts_block_text: str | None = None
    try:
        from app.services.artifacts.grounding import (
            resolve_active_artifacts,
            format_active_artifacts_block,
        )
        active_artifacts = await resolve_active_artifacts(
            db=db,
            tenant_id=tenant_id,
            chat_id=chat_id,
            user_content=user_content,
        )
        if active_artifacts:
            active_artifacts_block_text = format_active_artifacts_block(active_artifacts)
            logger.info(
                "[%s] grounded %d artifact(s): %s",
                correlation_id,
                len(active_artifacts),
                ", ".join(f"{a.kind}:{str(a.id)[:8]}" for a in active_artifacts),
            )
    except Exception:
        logger.exception("[pipeline] artifact auto-grounding failed (non-fatal)")

    # === [BLOCK-HISTORY-RESUMES] — agentic memory ===
    # Inject the last N pair-resumes (user-question summary + assistant-response summary)
    # as a compact markdown block, BEFORE building the system message. Full original
    # content is reachable via recall_chat and get_message tools.
    # N = config.max_context_messages (treated as pair-count).
    try:
        n_pairs = max(0, int(getattr(config, "max_context_messages", 0) or 0))
        if n_pairs > 0:
            recent_user_q = (
                select(Message)
                .where(
                    Message.tenant_id == tenant_id,
                    Message.chat_id == chat_id,
                    Message.role == "user",
                    Message.resume_query.is_not(None),
                )
                .order_by(Message.created_at.desc())
                .limit(n_pairs + 1)  # +1 to potentially drop current user msg if it slipped in
            )
            user_rows = list(reversed((await db.execute(recent_user_q)).scalars().all()))
            # Exclude the current user message if present (resume for it isn't relevant —
            # the model already sees the full text in the user turn).
            if user_message_id:
                cur_id_s = str(user_message_id)
                user_rows = [u for u in user_rows if str(u.id) != cur_id_s]
            user_rows = user_rows[-n_pairs:]

            resume_lines: list[str] = []
            for u in user_rows:
                asst = (await db.execute(
                    select(Message).where(
                        Message.chat_id == chat_id,
                        Message.role == "assistant",
                        Message.created_at >= u.created_at,
                    ).order_by(Message.created_at.asc()).limit(1)
                )).scalar_one_or_none()
                resp_resume = (asst.resume_response if asst else None) or "(нет резюме ответа)"
                q_resume = (u.resume_query or "").strip() or "(нет резюме)"
                # Anchor the id on the assistant message when present — that's the
                # row that owns artifacts, and the row the model fetches via get_message.
                anchor_id = str(asst.id) if asst else str(u.id)
                resume_lines.append(f"- [{anchor_id}] Q: {q_resume} → A: {resp_resume}")
                # 📎 markers: one line per artifact attached to the assistant reply.
                # The JSONB now stores refs to rows in `artifacts` — include the
                # artifact_id so the model can fetch it via get_artifact(id).
                arts = (asst.artifacts if asst else None) or []
                for a in arts:
                    kind = (a.get("kind") or "").strip() or "code"
                    label = (a.get("label") or "").strip()
                    aid = (a.get("id") or "").strip()
                    if not label:
                        continue
                    if aid:
                        resume_lines.append(f"  📎 [{kind}] {label} (artifact_id={aid})")
                    else:
                        resume_lines.append(f"  📎 [{kind}] {label}")
            if resume_lines:
                system_parts.append(
                    "## Recent conversation (резюме последних обменов)\n"
                    + "\n".join(resume_lines)
                    + "\n\nРезюме не содержат конкретных значений (IP, числа, имена) — это специально, "
                    + "чтобы исключить искажения. Конкретику бери ТОЛЬКО из:\n"
                    + "- блока «Активные артефакты» (если есть),\n"
                    + "- вызова `get_artifact(artifact_id)` для маркера 📎 из списка выше,\n"
                    + "- `find_artifacts(kind=..., query=...)` если артефакт не упомянут,\n"
                    + "- `recall_chat` / `get_message(id)` для контекста самого обмена."
                )
    except Exception:
        logger.exception("[pipeline] failed to assemble HISTORY-RESUMES block")

    messages: list[dict] = []
    if system_parts:
        messages.append({"role": "system", "content": "\n\n".join(system_parts)})

    # Build history: saved summary (from DB) + recent messages in full.
    # Summary is generated/updated in background — zero LLM calls here.
    chat = (await db.execute(select(Chat).where(Chat.id == chat_id))).scalar_one_or_none()
    context_mode = _normalize_context_mode(config.context_mode)
    history_dicts = _build_history_dicts(recent_msgs)

    # ========================================================================
    # MINIMAL PROMPT MODE — full-history fallback OFF.
    # The HISTORY-RESUMES block above replaces this; legacy code kept for fallback.
    # ========================================================================

    if False:  # === [BLOCK-HISTORY] OLD full-history mode (kept for fallback) ===
        # Prepend saved summary if exists (covers older conversation context)
        if context_mode != "recent_only" and chat and chat.history_summary:
            summary_up_to = max(chat.history_summary_up_to or 0, 0)
            unsummarized_count = max(total_messages_count - summary_up_to - 1, 0)
            if context_mode == "summary_only":
                history_dicts = []
            elif unsummarized_count <= 0:
                history_dicts = []
            elif unsummarized_count < len(history_dicts):
                history_dicts = history_dicts[-unsummarized_count:]
            messages.append({"role": "user", "content": f"[Краткое содержание предыдущего диалога]\n{chat.history_summary}"})
            messages.append({"role": "assistant", "content": "Понял, учитываю контекст."})
            logger.debug(f"[{correlation_id}] Using saved history summary + {len(history_dicts)} recent messages")
        elif context_mode == "summary_only":
            history_dicts = []

        if needs_tools:
            history_dicts = _compact_history_for_tool_request(history_dicts, user_content)

        if needs_tools:
            history_reference = _format_history_reference_block(history_dicts)
            if history_reference:
                messages.append({"role": "system", "content": history_reference})
        else:
            # Add all recent messages in their original chat roles for normal dialog mode.
            for m in history_dicts:
                messages.append(m)

    # History summary update — DISABLED for local Ollama to avoid queue contention.
    # Summary will be generated via admin endpoint or when using fast API provider.

    # Append current user message explicitly (excluded from DB query to avoid duplication).
    # If model supports vision and chat has image attachments, attach them inline.
    image_payloads: list[tuple[str, bytes, str]] = []
    if resolved.supports_vision and chat_attachments:
        image_payloads = await _build_image_attachments_for_llM(
            chat_id=chat_id,
            tenant_id=tenant_id,
            user_message_id=user_message_id,
            chat_attachments=chat_attachments,
            db=db,
        )
        if image_payloads:
            logger.info(
                f"[{correlation_id}] Attaching {len(image_payloads)} image(s) inline to LLM "
                f"(model={model_name}, provider={config.provider_type})"
            )

    # Files attached to THIS message — inline their text (summary or full body for
    # small files) right before the question. Drop image-files when the model is
    # actually consuming them as multimodal images (no need to duplicate as text).
    inline_attachments = current_message_attachments
    if image_payloads:
        inline_attachments = [a for a in inline_attachments if a.file_type != "image"]
    current_block = _build_current_attachments_block(inline_attachments, tools_enabled)
    # Stack order matters for attention: artifacts (the "open file") first,
    # then current-message attachments, then the question. Mirrors how Cursor
    # presents context to the model.
    composed_parts: list[str] = []
    if active_artifacts_block_text:
        composed_parts.append(active_artifacts_block_text)
    if current_block:
        composed_parts.append(current_block)
    composed_parts.append(user_content)
    composed_user_content = "\n\n".join(composed_parts)

    if image_payloads:
        messages.append(
            _build_user_message_with_images(
                composed_user_content,
                image_payloads,
                config.provider_type,
            )
        )
    else:
        messages.append({"role": "user", "content": composed_user_content})

    # Merge tenant tools + attachment search tools only when the request and model support tools.
    all_tool_defs = [_public_tool_def(t.config_json) for t in tools if t.config_json] if tools else []
    all_tool_defs = all_tool_defs + attachment_tool_defs
    # Builtin tools — system toolset (memory/artifacts/RAG). Always exposed
    # to the model regardless of semantic budget; lives in code, not DB.
    if tools_enabled:
        from app.services.tools.builtin_registry import builtin_tools_for_payload
        all_tool_defs = builtin_tools_for_payload() + all_tool_defs

    if all_tool_defs and tools_enabled:
        tool_defs = trim_tool_definitions(all_tool_defs)
    else:
        tool_defs = None
        if all_tool_defs and not tools_enabled:
            logger.debug(f"[{correlation_id}] Skipped {len(all_tool_defs)} tools — disabled for this request/model")

    # Per-section token breakdown (approx via tiktoken). Telemetry only —
    # provider's usage.prompt_tokens remains the source of truth for billing.
    _tokens_memory = _ct(_memory_block_text)
    _tokens_kb = _ct(_kb_block_text)
    _tokens_system_total = 0
    for m in messages:
        if m.get("role") != "system":
            continue
        c = m.get("content")
        if isinstance(c, str):
            _tokens_system_total += _ct(c)
    _tokens_attachments = _ct(_attachments_block_text)
    _tokens_system = max(0, _tokens_system_total - _tokens_memory - _tokens_kb - _tokens_attachments)
    _tokens_tools = _ct_obj(tool_defs) if tool_defs else 0
    _tokens_history = 0
    for m in messages[:-1]:  # skip current user (last)
        if m.get("role") == "system":
            continue
        c = m.get("content")
        if isinstance(c, str):
            _tokens_history += _ct(c)
        elif isinstance(c, list):
            for part in c:
                if isinstance(part, dict) and part.get("type") == "text":
                    _tokens_history += _ct(part.get("text"))
    _tokens_user = _ct(user_content)

    # 8. Call provider
    total_prompt_tokens = 0
    total_completion_tokens = 0
    tool_calls_total = 0
    tool_outputs_current_request: list[dict[str, str]] = []

    start = time.time()
    resp = None
    error_text = None
    status = "success"

    # Build a per-round chunk emitter that the provider invokes for each delta
    current_round_ref = {"round": 0}
    first_chunk_at: float | None = None

    async def _on_chunk(chunk: dict) -> None:
        nonlocal first_chunk_at
        ctype = chunk.get("type", "content")
        text = chunk.get("text", "")
        if not text:
            return
        if first_chunk_at is None:
            first_chunk_at = time.time()
        if ctype == "reasoning":
            await _emit("reasoning_chunk", {"round": current_round_ref["round"], "text": text})
        else:
            await _emit("content_chunk", {"round": current_round_ref["round"], "text": text})

    chunk_cb = _on_chunk if on_event is not None else None

    try:
        # Initial LLM call
        await _emit("provider_call_start", {"round": 0, "model": model_name})
        provider_t0 = time.time()
        current_round_ref["round"] = 0
        resp = await provider.chat_completion(
            messages=messages,
            model=model_name,
            temperature=effective_temperature,
            max_tokens=config.max_tokens,
            tools=tool_defs,
            on_chunk=chunk_cb,
            extra_body=_resolve_thinking_kwargs(
                getattr(config, "enable_thinking", "on"),
                user_content,
                bool(tool_defs),
            ),
        )
        await _emit("provider_call_done", {
            "round": 0,
            "latency_ms": int((time.time() - provider_t0) * 1000),
            "has_tool_calls": bool(resp.tool_calls),
            "content_chars": len(resp.content or ""),
            "reasoning_chars": len(resp.reasoning or ""),
        })
        if resp.reasoning:
            await _emit("reasoning", {"round": 0, "text": resp.reasoning})

        if resp.prompt_tokens:
            total_prompt_tokens += resp.prompt_tokens
        if resp.completion_tokens:
            total_completion_tokens += resp.completion_tokens

        # Tool execution loop
        round_num = 0
        while resp.tool_calls and round_num < MAX_TOOL_ROUNDS:
            round_num += 1
            tool_calls_total += len(resp.tool_calls)

            logger.debug(f"[{correlation_id}] Tool round {round_num}: {len(resp.tool_calls)} call(s)")

            # Add assistant message with tool_calls to conversation —
            # provider decides what extra fields (reasoning_content, etc.) to echo.
            messages.append(provider.format_assistant_turn(resp))

            # Execute each tool call and add results
            for tc in resp.tool_calls:
                # Parse tool call — handle both Ollama and OpenAI formats
                if isinstance(tc, dict):
                    # OpenAI format: {"id": "...", "type": "function", "function": {"name": "...", "arguments": "..."}}
                    func_info = tc.get("function", tc)
                    tool_call_id = tc.get("id", str(uuid.uuid4()))
                    func_name = func_info.get("name", "")
                    func_args = func_info.get("arguments", {})
                    if isinstance(func_args, str):
                        try:
                            func_args = json.loads(func_args)
                        except json.JSONDecodeError:
                            func_args = {"raw": func_args}
                else:
                    continue

                logger.debug(f"[{correlation_id}] Executing tool: {func_name}({func_args})")

                if func_name not in allowed_tool_names:
                    logger.warning(f"[{correlation_id}] Blocked tool call not allowed for tenant/chat: {func_name}")
                    tool_output = f"Ошибка: инструмент '{func_name}' недоступен для этого tenant."
                    tool_outputs_current_request.append({"tool": func_name, "output": tool_output})
                    messages.append(provider.format_tool_result_turn(
                        tool_call_id=tool_call_id,
                        content=tool_output,
                    ))
                    continue

                # Check if this is an attachment search tool
                await _emit("tool_call_start", {
                    "name": func_name,
                    "round": round_num,
                    "args_preview": json.dumps(func_args, ensure_ascii=False)[:300],
                })
                tool_t0 = time.time()
                tool_ok = True
                if func_name in attachment_map:
                    from app.services.attachments.tool import execute_attachment_search
                    from app.core.config import settings as app_settings
                    att_embed_provider = get_provider("ollama", app_settings.OLLAMA_BASE_URL or "http://localhost:11434")
                    att_query = func_args.get("query", "")
                    tool_output = await execute_attachment_search(
                        attachment_id=attachment_map[func_name],
                        query=att_query,
                        db=db,
                        provider=att_embed_provider,
                        embedding_model=config.embedding_model_name or "nomic-embed-text",
                    )
                else:
                    # Execute regular tool
                    # Tool config may come from the semantic-selected payload OR
                    # from the full tenant allow-set (model invoked something we didn't
                    # send). Use whichever exists; also register it into the payload
                    # for subsequent rounds so the model sees the real schema.
                    _cfg = tool_config_map.get(func_name) or all_allowed_tools_for_tenant.get(func_name)
                    if _cfg is not None and func_name not in tool_config_map:
                        tool_config_map[func_name] = _cfg
                        if tool_defs is not None and _cfg.get("function"):
                            tool_defs.append({"type": _cfg.get("type", "function"), "function": _cfg["function"]})
                    result = await execute_tool(func_name, func_args, _cfg)
                    tool_ok = result.success
                    tool_output = result.output if result.success else f"Ошибка: {result.error}"

                await _emit("tool_call_done", {
                    "name": func_name,
                    "round": round_num,
                    "ok": tool_ok,
                    "latency_ms": int((time.time() - tool_t0) * 1000),
                    "output_chars": len(tool_output or ""),
                    "output_tokens": _ct(tool_output or ""),
                })
                logger.debug(f"[{correlation_id}] Tool result ({len(tool_output)} chars): {tool_output[:200]}")
                tool_outputs_current_request.append({"tool": func_name, "output": tool_output})

                # Promote successful, substantial tool results to first-class
                # artifacts. Without this, results die at the end of this round
                # and the next user turn ("оформи это в таблицу") has nothing
                # to ground on — leading to hallucinated values.
                if tool_ok:
                    try:
                        from app.services.artifacts.tool_result_capture import capture_tool_result_as_artifact
                        asyncio.create_task(capture_tool_result_as_artifact(
                            tenant_id=tenant_id,
                            chat_id=chat_id,
                            user_message_id=uuid.UUID(str(user_message_id)) if user_message_id else None,
                            tool_name=func_name,
                            arguments=func_args,
                            output=tool_output or "",
                        ))
                    except Exception:
                        logger.exception("[%s] tool-result capture scheduling failed (non-fatal)", correlation_id)

                # Add tool result to messages (full content for current round) —
                # provider decides shape (Ollama omits tool_call_id, OpenAI includes it).
                messages.append(provider.format_tool_result_turn(
                    tool_call_id=tool_call_id,
                    content=tool_output,
                ))

            # Summarize large tool results from PREVIOUS rounds to save tokens.
            # Current round results stay full so LLM can process them now.
            # After LLM sees them, they'll be summarized in the next iteration.
            summary_prompt_tokens, summary_completion_tokens = await _summarize_old_tool_results(
                messages, round_num, provider, model_name, correlation_id
            )
            total_prompt_tokens += summary_prompt_tokens
            total_completion_tokens += summary_completion_tokens

            # Call LLM again with tool results
            await _emit("provider_call_start", {"round": round_num, "model": model_name})
            provider_t0 = time.time()
            current_round_ref["round"] = round_num
            resp = await provider.chat_completion(
                messages=messages,
                model=model_name,
                temperature=effective_temperature,
                max_tokens=config.max_tokens,
                tools=tool_defs,
                on_chunk=chunk_cb,
                extra_body=_resolve_thinking_kwargs(
                    getattr(config, "enable_thinking", "on"),
                    user_content,
                    bool(tool_defs),
                ),
            )
            await _emit("provider_call_done", {
                "round": round_num,
                "latency_ms": int((time.time() - provider_t0) * 1000),
                "has_tool_calls": bool(resp.tool_calls),
                "content_chars": len(resp.content or ""),
                "reasoning_chars": len(resp.reasoning or ""),
            })
            if resp.reasoning:
                await _emit("reasoning", {"round": round_num, "text": resp.reasoning})

            if resp.prompt_tokens:
                total_prompt_tokens += resp.prompt_tokens
            if resp.completion_tokens:
                total_completion_tokens += resp.completion_tokens

        # ANTI-LAZY auto-nudge: model promised an action ("сейчас проверю / подождите")
        # but did not call any tool. Disabled by default for DeepSeek/Qwen2.5 —
        # they don't suffer from Qwen3-style lazy mode, and the nudge can cause
        # runaway tool spam when the regex matches benign content like "проверим что".
        if (
            ANTI_LAZY_ENABLED
            and resp
            and not resp.tool_calls
            and tool_defs
            and _is_lazy_response(resp.content or "")
            and round_num < MAX_TOOL_ROUNDS
        ):
            logger.info(
                f"[{correlation_id}] anti-lazy nudge: model wrote lazy intent without tool_call, content={(resp.content or '')[:120]!r}"
            )
            await _emit("anti_lazy_nudge", {"round": round_num + 1, "preview": (resp.content or "")[:160]})
            # Keep the lazy assistant turn in history so the model sees its own promise.
            messages.append(provider.format_assistant_turn(resp))
            messages.append({
                "role": "user",
                "content": (
                    "Ты только что написал, что выполнишь действие, но не вызвал tool. "
                    "ВЫЗОВИ нужный tool ПРЯМО СЕЙЧАС в этом ответе. "
                    "Не пиши «сейчас вызову», «подождите», не объясняй — просто tool_call."
                ),
            })
            round_num += 1
            await _emit("provider_call_start", {"round": round_num, "model": model_name})
            provider_t0 = time.time()
            current_round_ref["round"] = round_num
            resp = await provider.chat_completion(
                messages=messages,
                model=model_name,
                temperature=effective_temperature,
                max_tokens=config.max_tokens,
                tools=tool_defs,
                on_chunk=chunk_cb,
                extra_body=_resolve_thinking_kwargs(
                    getattr(config, "enable_thinking", "on"),
                    user_content,
                    bool(tool_defs),
                ),
            )
            await _emit("provider_call_done", {
                "round": round_num,
                "latency_ms": int((time.time() - provider_t0) * 1000),
                "has_tool_calls": bool(resp.tool_calls),
                "content_chars": len(resp.content or ""),
                "reasoning_chars": len(resp.reasoning or ""),
                "after_nudge": True,
            })
            if resp.reasoning:
                await _emit("reasoning", {"round": round_num, "text": resp.reasoning})
            if resp.prompt_tokens:
                total_prompt_tokens += resp.prompt_tokens
            if resp.completion_tokens:
                total_completion_tokens += resp.completion_tokens
            # If the model NOW returned tool_calls — re-enter the tool loop for them.
            while resp.tool_calls and round_num < MAX_TOOL_ROUNDS:
                round_num += 1
                tool_calls_total += len(resp.tool_calls)
                messages.append(provider.format_assistant_turn(resp))
                for tc in resp.tool_calls:
                    if not isinstance(tc, dict):
                        continue
                    func = tc.get("function") or {}
                    func_name = func.get("name")
                    func_args_raw = func.get("arguments") or "{}"
                    tool_call_id = tc.get("id")
                    try:
                        func_args = json.loads(func_args_raw) if isinstance(func_args_raw, str) else (func_args_raw or {})
                    except json.JSONDecodeError:
                        func_args = {}
                    tool_t0 = time.time()
                    await _emit("tool_call_start", {"name": func_name, "round": round_num, "args": redact_for_log(func_args)})
                    if func_name not in allowed_tool_names:
                        tool_output = f"Ошибка: инструмент '{func_name}' недоступен."
                        tool_ok = False
                    else:
                        try:
                            _cfg = tool_config_map.get(func_name) or all_allowed_tools_for_tenant.get(func_name)
                            if _cfg is not None and func_name not in tool_config_map:
                                tool_config_map[func_name] = _cfg
                                if tool_defs is not None and _cfg.get("function"):
                                    tool_defs.append({"type": _cfg.get("type", "function"), "function": _cfg["function"]})
                            result = await execute_tool(func_name, func_args, _cfg)
                            tool_ok = result.success
                            tool_output = result.output if result.success else f"Ошибка: {result.error}"
                        except Exception as e:
                            tool_output = f"Ошибка выполнения: {e}"
                            tool_ok = False
                    await _emit("tool_call_done", {
                        "name": func_name, "round": round_num, "ok": tool_ok,
                        "latency_ms": int((time.time() - tool_t0) * 1000),
                        "output_chars": len(tool_output or ""),
                        "output_tokens": _ct(tool_output or ""),
                    })
                    tool_outputs_current_request.append({"tool": func_name, "output": tool_output})
                    # Capture successful tool result as Artifact (auto-grounding ready).
                    if tool_ok:
                        try:
                            from app.services.artifacts.tool_result_capture import capture_tool_result_as_artifact
                            asyncio.create_task(capture_tool_result_as_artifact(
                                tenant_id=tenant_id,
                                chat_id=chat_id,
                                user_message_id=uuid.UUID(str(user_message_id)) if user_message_id else None,
                                tool_name=func_name,
                                arguments=func_args,
                                output=tool_output or "",
                            ))
                        except Exception:
                            logger.exception("[%s] tool-result capture scheduling failed (non-fatal)", correlation_id)
                    messages.append(provider.format_tool_result_turn(
                        tool_call_id=tool_call_id,
                        content=tool_output,
                    ))
                summary_prompt_tokens, summary_completion_tokens = await _summarize_old_tool_results(
                    messages, round_num, provider, model_name, correlation_id
                )
                total_prompt_tokens += summary_prompt_tokens
                total_completion_tokens += summary_completion_tokens
                await _emit("provider_call_start", {"round": round_num, "model": model_name})
                provider_t0 = time.time()
                current_round_ref["round"] = round_num
                resp = await provider.chat_completion(
                    messages=messages,
                    model=model_name,
                    temperature=effective_temperature,
                    max_tokens=config.max_tokens,
                    tools=tool_defs,
                    on_chunk=chunk_cb,
                    extra_body=_resolve_thinking_kwargs(
                        getattr(config, "enable_thinking", "on"),
                        user_content,
                        bool(tool_defs),
                    ),
                )
                await _emit("provider_call_done", {
                    "round": round_num,
                    "latency_ms": int((time.time() - provider_t0) * 1000),
                    "has_tool_calls": bool(resp.tool_calls),
                    "content_chars": len(resp.content or ""),
                    "reasoning_chars": len(resp.reasoning or ""),
                })
                if resp.reasoning:
                    await _emit("reasoning", {"round": round_num, "text": resp.reasoning})
                if resp.prompt_tokens:
                    total_prompt_tokens += resp.prompt_tokens
                if resp.completion_tokens:
                    total_completion_tokens += resp.completion_tokens

        # If we exhausted MAX_TOOL_ROUNDS while the model still wanted more
        # tools (and produced no useful content), force one more LLM call
        # WITHOUT tools — instructing it to summarize what it has so the user
        # gets at least a partial answer instead of a blank assistant message.
        if (
            resp
            and resp.tool_calls
            and round_num >= MAX_TOOL_ROUNDS
            and not (resp.content or "").strip()
        ):
            logger.info(
                f"[{correlation_id}] Tool rounds exhausted ({MAX_TOOL_ROUNDS}); "
                f"forcing summary call without tools"
            )
            # Add the unfinished assistant turn so history is consistent,
            # then a system nudge to wrap up.
            messages.append(provider.format_assistant_turn(resp))
            # Provide synthetic tool results (empty) so the schema is valid for
            # providers that strictly require tool_call_id pairing.
            for tc in resp.tool_calls:
                if isinstance(tc, dict):
                    tc_id = tc.get("id", str(uuid.uuid4()))
                    messages.append(provider.format_tool_result_turn(
                        tool_call_id=tc_id,
                        content="(Лимит вызовов инструментов исчерпан. Дай ответ на основе того, что уже собрано.)",
                    ))
            messages.append({
                "role": "system",
                "content": (
                    "Достигнут лимит вызовов инструментов в этом раунде. "
                    "Сформулируй ответ пользователю на основе уже полученных данных. "
                    "Если данных недостаточно — честно скажи об этом и предложи следующие шаги. "
                    "НЕ вызывай tools в этом ответе."
                ),
            })
            await _emit("provider_call_start", {"round": round_num + 1, "model": model_name, "final_summary": True})
            provider_t0 = time.time()
            current_round_ref["round"] = round_num + 1
            try:
                resp = await provider.chat_completion(
                    messages=messages,
                    model=model_name,
                    temperature=effective_temperature,
                    max_tokens=config.max_tokens,
                    tools=None,  # explicitly disable tools
                    on_chunk=chunk_cb,
                    extra_body={"chat_template_kwargs": {"enable_thinking": False}},  # final summary fast
                )
                await _emit("provider_call_done", {
                    "round": round_num + 1,
                    "latency_ms": int((time.time() - provider_t0) * 1000),
                    "has_tool_calls": False,
                    "content_chars": len(resp.content or ""),
                    "reasoning_chars": len(resp.reasoning or ""),
                    "final_summary": True,
                })
                if resp.prompt_tokens:
                    total_prompt_tokens += resp.prompt_tokens
                if resp.completion_tokens:
                    total_completion_tokens += resp.completion_tokens
            except Exception as e:
                logger.warning(f"[{correlation_id}] Final summary call failed: {e}")

        latency = (time.time() - start) * 1000

    except Exception as e:
        latency = (time.time() - start) * 1000
        status = "error"
        error_text = f"{type(e).__name__}: {e}"
        logger.error(f"[{correlation_id}] LLM call failed after {latency:.0f}ms: {error_text}", exc_info=True)

    # 8. Save log
    # Strip large base64 image payloads before logging
    log_messages = _strip_image_data_from_messages(messages)
    _logged_extra = _resolve_thinking_kwargs(
        getattr(config, "enable_thinking", "on"),
        user_content,
        bool(tool_defs),
    )
    raw_req_obj: dict = {
        "messages": log_messages,
        "model": model_name,
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
        "tools": tool_defs,
    }
    if _logged_extra:
        raw_req_obj.update(_logged_extra)  # includes chat_template_kwargs (enable_thinking, thinking_budget)
    raw_req = redact_for_log(raw_req_obj)
    raw_resp = redact_for_log(resp.raw_response) if resp and resp.raw_response else None
    req_bytes = len(json.dumps(messages).encode()) if messages else None
    resp_bytes = len(resp.content.encode()) if resp else None

    total_tokens = (total_prompt_tokens + total_completion_tokens) if (total_prompt_tokens or total_completion_tokens) else None
    time_to_first_token_ms = int((first_chunk_at - start) * 1000) if first_chunk_at is not None else None

    # Build normalized with tool execution details and token breakdown
    def _content_to_text(c) -> str:
        if isinstance(c, str):
            return c
        if isinstance(c, list):
            parts = []
            for p in c:
                if isinstance(p, dict) and p.get("type") == "text":
                    parts.append(p.get("text", ""))
            return " ".join(parts)
        return ""

    system_content = ""
    history_content = ""
    for m in messages:
        if m["role"] == "system":
            system_content = _content_to_text(m.get("content", ""))
        elif m["role"] in ("user", "assistant"):
            history_content += _content_to_text(m.get("content", "")) + " "

    system_prompt_chars = len(config.system_prompt or "")
    rules_chars = len(config.rules_text or "")
    memory_chars = sum(len(m.content or "") for m in memory_entries)
    kb_chars = sum(len(c.content or "") for c in kb_chunks)
    history_chars = len(history_content)
    tools_chars = len(json.dumps(tool_defs)) if tool_defs else 0

    # Approximate token counts (1 token ≈ 3.5 chars for multilingual text)
    TOKEN_RATIO = 3.5
    context_breakdown = {
        "system_prompt": {"chars": system_prompt_chars, "est_tokens": int(system_prompt_chars / TOKEN_RATIO)},
        "rules": {"chars": rules_chars, "est_tokens": int(rules_chars / TOKEN_RATIO)},
        "memory": {"chars": memory_chars, "entries": len(memory_entries), "est_tokens": int(memory_chars / TOKEN_RATIO)},
        "kb": {"chars": kb_chars, "chunks": len(kb_chunks), "est_tokens": int(kb_chars / TOKEN_RATIO)},
        "history": {"chars": history_chars, "messages": len([m for m in messages if m["role"] != "system"]), "est_tokens": int(history_chars / TOKEN_RATIO)},
        "tools": {"chars": tools_chars, "count": len(tool_defs) if tool_defs else 0, "est_tokens": int(tools_chars / TOKEN_RATIO)},
    }
    total_est_tokens = sum(v["est_tokens"] for v in context_breakdown.values())
    context_breakdown["total_est_tokens"] = total_est_tokens

    logger.debug(f"[{correlation_id}] Context breakdown: "
                f"system={context_breakdown['system_prompt']['est_tokens']}t, "
                f"rules={context_breakdown['rules']['est_tokens']}t, "
                f"memory={context_breakdown['memory']['est_tokens']}t({len(memory_entries)}), "
                f"kb={context_breakdown['kb']['est_tokens']}t({len(kb_chunks)}chunks), "
                f"history={context_breakdown['history']['est_tokens']}t({context_breakdown['history']['messages']}msgs), "
                f"tools={context_breakdown['tools']['est_tokens']}t({context_breakdown['tools']['count']}), "
                f"TOTAL≈{total_est_tokens}t")

    norm_req = {
        "messages_count": len(messages),
        "model": model_name,
        "tools_count": len(tool_defs) if tool_defs else 0,
        "tool_rounds": tool_calls_total,
        "context_breakdown": context_breakdown,
        "prompt_layout": _build_prompt_layout(messages, tool_defs, tool_mode=needs_tools),
    }
    norm_resp: dict = {"content_length": len(resp.content) if resp else 0}
    if tool_calls_total > 0:
        # Include tool call details in normalized response.
        # If the result is JSON, store the parsed object so the UI can render it
        # as a table without truncation breaking JSON.parse.
        # For tabular results — keep first TOOL_LOG_MAX_ROWS rows; preserve real count.
        TOOL_LOG_RAW_LIMIT = 20000
        TOOL_LOG_MAX_ROWS = 20

        def _truncate_table_payload(obj):
            if isinstance(obj, list) and obj and all(isinstance(x, dict) for x in obj):
                if len(obj) <= TOOL_LOG_MAX_ROWS:
                    return obj
                return {
                    "count": len(obj),
                    "items": obj[:TOOL_LOG_MAX_ROWS],
                    "log_truncated": True,
                    "log_shown_rows": TOOL_LOG_MAX_ROWS,
                }
            if isinstance(obj, dict) and isinstance(obj.get("items"), list):
                items = obj["items"]
                if all(isinstance(x, dict) for x in items) and len(items) > TOOL_LOG_MAX_ROWS:
                    truncated = dict(obj)
                    truncated["items"] = items[:TOOL_LOG_MAX_ROWS]
                    truncated["log_truncated"] = True
                    truncated["log_shown_rows"] = TOOL_LOG_MAX_ROWS
                    if "count" not in truncated:
                        truncated["count"] = len(items)
                    return truncated
            return obj

        def _store_tool_content(raw: str):
            if not isinstance(raw, str) or not raw:
                return raw
            stripped = raw.lstrip()
            if stripped.startswith("{") or stripped.startswith("["):
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, (dict, list)):
                        return _truncate_table_payload(parsed)
                except (ValueError, TypeError):
                    pass
            if len(raw) > TOOL_LOG_RAW_LIMIT:
                return raw[:TOOL_LOG_RAW_LIMIT] + "\n... [обрезано в логе]"
            return raw

        tool_log = []
        for m in messages:
            if m.get("role") == "tool":
                tool_log.append({"role": "tool", "content": _store_tool_content(m.get("content", ""))})
            elif m.get("role") == "assistant" and m.get("tool_calls"):
                calls = []
                for tc in m["tool_calls"]:
                    func = tc.get("function", tc)
                    calls.append({"name": func.get("name"), "arguments": func.get("arguments")})
                tool_log.append({"role": "assistant_tool_calls", "calls": calls})
        norm_resp["tool_execution"] = tool_log

    message_uuid = None
    if user_message_id:
        try:
            message_uuid = uuid.UUID(str(user_message_id))
        except (ValueError, TypeError):
            message_uuid = None

    log = LLMRequestLog(
        tenant_id=tenant_id,
        chat_id=chat_id,
        api_key_id=uuid.UUID(str(api_key_id)) if api_key_id else None,
        message_id=message_uuid,
        correlation_id=correlation_id,
        provider_type=resolved.provider_type,
        model_name=model_name,
        raw_request=raw_req,
        raw_response=raw_resp,
        normalized_request=norm_req,
        normalized_response=norm_resp,
        status=status,
        error_text=error_text,
        latency_ms=latency,
        time_to_first_token_ms=time_to_first_token_ms,
        prompt_tokens=total_prompt_tokens or None,
        completion_tokens=total_completion_tokens or None,
        total_tokens=total_tokens,
        request_size_bytes=req_bytes,
        response_size_bytes=resp_bytes,
        tool_calls_count=tool_calls_total,
        finish_reason=resp.finish_reason if resp else None,
        context_messages_count=len(messages),
        context_memory_count=len(memory_entries),
        context_kb_count=len(kb_chunks),
        context_tools_count=len(tools),
        tokens_system=_tokens_system or None,
        tokens_tools=_tokens_tools or None,
        tokens_memory=_tokens_memory or None,
        tokens_kb=_tokens_kb or None,
        tokens_history=_tokens_history or None,
        tokens_user=_tokens_user or None,
    )
    db.add(log)

    if not resp:
        raise ValueError(f"LLM call failed: {error_text}")

    response_summary = _compact_text(resp.content, max_chars=500)
    tool_result_summary = _build_tool_result_summary(tool_outputs_current_request)
    attachment_summary = _build_attachment_summary(chat_attachments)
    context_card = _build_context_card(response_summary, tool_result_summary, attachment_summary)
    history_exclude = _looks_garbled_text(resp.content)

    # 9. Background enrichment.
    if resp:
        summary_model = (getattr(config, "summary_model_name", None) or model_name).strip()

        if config.memory_enabled and resolved.provider_type != "ollama" and MEMORY_AUTO_EXTRACT:
            asyncio.create_task(
                _extract_memory_background(
                    provider,
                    summary_model,
                    tenant_id,
                    chat_id,
                    user_content,
                    resp.content,
                )
            )

        history_for_summary = history_dicts + [
            {"role": "user", "content": user_content},
            {"role": "assistant", "content": resp.content},
        ]
        if len(history_for_summary) > RECENT_MESSAGES_FULL:
            summary_target_count = max(total_messages_count + 1 - RECENT_MESSAGES_FULL, 0)
            asyncio.create_task(
                _update_history_summary_background(
                    chat_id=chat_id,
                    old_messages=history_for_summary[:-RECENT_MESSAGES_FULL],
                    existing_summary=chat.history_summary if chat else None,
                    provider=provider,
                    model_name=summary_model,
                    message_count_up_to=summary_target_count,
                )
            )

        if chat and (not chat.title or not chat.description):
            await _auto_summary_background(
                provider,
                config,
                chat_id,
                user_content,
                resp.content,
                fallback_model_name=summary_model,
            )

    # 11. Return response
    final_payload = {
        "content": resp.content,
        "prompt_tokens": total_prompt_tokens or resp.prompt_tokens,
        "completion_tokens": total_completion_tokens or resp.completion_tokens,
        "total_tokens": total_tokens,
        "latency_ms": latency,
        "time_to_first_token_ms": time_to_first_token_ms,
        "finish_reason": resp.finish_reason,
        "correlation_id": correlation_id,
        "provider_type": resolved.provider_type,
        "model_name": model_name,
        "tool_calls": resp.tool_calls,
        "tool_calls_count": tool_calls_total,
        "reasoning": resp.reasoning,
        "response_summary": response_summary,
        "tool_result_summary": tool_result_summary,
        "attachment_summary": attachment_summary,
        "context_card": context_card,
        "history_exclude": history_exclude,
    }
    await _emit("done", {
        "content": resp.content,
        "reasoning": resp.reasoning,
        "total_tokens": total_tokens,
        "prompt_tokens": final_payload["prompt_tokens"],
        "completion_tokens": final_payload["completion_tokens"],
        "tool_calls_count": tool_calls_total,
        "latency_ms": latency,
        "model_name": model_name,
    })
    return final_payload


HISTORY_SUMMARY_PROMPT = """Сожми историю диалога в краткое резюме (максимум 5-6 предложений).
Сохрани: ключевые вопросы пользователя, важные факты, результаты действий, решения.
Отбрось: приветствия, повторы, промежуточные рассуждения.
{existing}
Диалог:
{history}

Краткое резюме:"""


async def _update_history_summary_background(
    chat_id,
    old_messages: list[dict],
    existing_summary: str | None,
    provider,
    model_name,
    message_count_up_to: int | None = None,
):
    """Background: generate/update history summary and save to DB."""
    try:
        from app.core.database import async_session

        # Build history text for summarization
        lines = []
        for m in old_messages:
            role = "Пользователь" if m["role"] == "user" else "Ассистент"
            content = m.get("content", "")
            if len(content) > 500:
                content = content[:500] + "..."
            lines.append(f"{role}: {content}")
        history_text = "\n".join(lines[-30:])  # last 30 messages max

        existing_part = ""
        if existing_summary:
            existing_part = f"\nПредыдущее резюме (дополни его новыми фактами):\n{existing_summary}\n"

        prompt = HISTORY_SUMMARY_PROMPT.format(
            existing=existing_part,
            history=history_text[:4000],
        )

        resp = await provider.chat_completion(
            messages=[{"role": "user", "content": prompt}],
            model=model_name,
            temperature=0.2,
            max_tokens=400,
        )
        summary = resp.content.strip()
        if len(summary) > 1000:
            summary = summary[:1000]

        async with async_session() as db:
            chat = (await db.execute(select(Chat).where(Chat.id == chat_id))).scalar_one_or_none()
            if chat:
                chat.history_summary = summary
                chat.history_summary_up_to = message_count_up_to if message_count_up_to is not None else len(old_messages)
                await db.commit()
                logger.debug(
                    f"History summary updated for chat {chat_id}: {len(summary)} chars, "
                    f"up_to={chat.history_summary_up_to}"
                )
    except Exception:
        logger.debug("Background history summary update failed", exc_info=True)


async def _extract_memory_background(provider, model_name, tenant_id, chat_id, user_content, assistant_content):
    """Background task: extract memory facts without blocking the response."""
    try:
        from app.core.database import async_session
        async with async_session() as db:
            await _extract_memory(provider, model_name, tenant_id, chat_id, user_content, assistant_content, db)
            await db.commit()
    except Exception:
        logger.debug("Background memory extraction failed", exc_info=True)


def _pick_summary_model_name(config, fallback_model_name: str | None = None) -> str:
    explicit = str(getattr(config, "summary_model_name", None) or "").strip()
    if explicit:
        return explicit
    fallback = str(fallback_model_name or "").strip()
    if fallback:
        return fallback
    return str(getattr(config, "model_name", "") or "").strip()


async def _auto_summary_background(
    provider,
    config,
    chat_id,
    user_content,
    assistant_content,
    *,
    fallback_model_name: str | None = None,
):
    """Background task: auto-generate chat title without blocking the response."""
    try:
        from app.core.database import async_session
        from app.models.chat import Chat
        async with async_session() as db:
            chat = (await db.execute(select(Chat).where(Chat.id == chat_id))).scalar_one_or_none()
            if not chat or (chat.title and chat.description):
                return
            summary_model = _pick_summary_model_name(config, fallback_model_name)
            language_hint = _detect_title_language(user_content)
            summary = await provider.summarize(
                f"User message:\n{user_content}\n\nAssistant response:\n{assistant_content}",
                summary_model,
                language_hint=language_hint,
            )
            summary = summary[:200]
            if not chat.title:
                chat.title = summary
            if not chat.description:
                chat.description = summary
            await db.commit()
    except Exception:
        logger.debug("Background auto-summary failed", exc_info=True)


MEMORY_EXTRACTION_PROMPT = """Проанализируй диалог и извлеки ТОЛЬКО факты, которые НЕЛЬЗЯ восстановить через инструменты (поиск клиентов / адресов / оборудования) и которые будут полезны для БУДУЩИХ диалогов.

Верни ТОЛЬКО JSON-массив фактов. Каждый факт — объект:
- "fact": краткая формулировка (1 предложение, на русском)
- "type": "long_term" (постоянная характеристика) или "episodic" (контекст текущей сессии)

ИЗВЛЕКАЙ:
- Предпочтения пользователя в работе (как любит получать ответы, какие команды чаще использует).
- Принятые решения и выводы по инфраструктуре, которые НЕ записаны в БД (например: «свич X решено заменить через неделю», «магистраль на улице Y перегружена»).
- Имя/роль самого пользователя если он его сообщил («Меня зовут Артём, я админ сети»).
- Названия проектов / зон ответственности пользователя.

НЕ ИЗВЛЕКАЙ:
- Данные клиентов: ФИО, телефон, адрес, договор, тариф, услуга, MAC, IP — это всё в БД, доступно через tools.
- Параметры конкретного оборудования (id свича, IP, vendor) — берётся через tools.
- Результаты диагностики (порт N в forwarding, ONU online) — meaningless вне контекста.
- Промежуточные шаги диалога, цитаты из ответов модели, обрывки tool-вывода.
- Общеизвестную информацию.

Если ничего из РАЗРЕШЁННОЙ категории нет — верни []. Не придумывай.

Диалог:
User: {user_message}
Assistant: {assistant_message}

JSON:"""

# Heuristics to drop low-value extracted facts that slipped through the prompt.
_MEMORY_BLOCK_PATTERNS = [
    r"клиент(а|у)?\s",
    r"\bФИО\b",
    r"номер\s+(договор|телефон)",
    r"\bMAC\b",
    r"IP\s*[-:]?\s*\d",
    r"\bport\b|\bпорт\s*\d",
    r"\bONU\b",
    r"тариф",
    r"услуг(а|и)",
    r"подключен\s+через",
    r"в\s+статусе\s+(forward|down|up)",
    r"оборудовани(е|и)",
]
import re as _re
_MEMORY_BLOCK_RE = _re.compile("|".join(_MEMORY_BLOCK_PATTERNS), _re.IGNORECASE)


async def _extract_memory(
    provider,
    model_name: str,
    tenant_id: str,
    chat_id: str,
    user_content: str,
    assistant_content: str,
    db: AsyncSession,
):
    """Extract facts from dialogue and save to memory automatically."""
    try:
        prompt = MEMORY_EXTRACTION_PROMPT.format(
            user_message=user_content[:1000],
            assistant_message=assistant_content[:1000],
        )
        resp = await provider.chat_completion(
            messages=[{"role": "user", "content": prompt}],
            model=model_name,
            temperature=0.1,
            max_tokens=500,
        )

        text = resp.content.strip()
        # Extract JSON from response (handle markdown code blocks)
        if "```" in text:
            import re
            match = re.search(r'```(?:json)?\s*(\[.*?\])\s*```', text, re.DOTALL)
            if match:
                text = match.group(1)

        facts = json.loads(text)
        if not isinstance(facts, list) or not facts:
            return

        # Check for duplicates before saving
        existing_q = select(MemoryEntry.content).where(
            MemoryEntry.tenant_id == tenant_id,
            MemoryEntry.deleted_at.is_(None),
        )
        existing_contents = set(
            r[0].lower().strip()
            for r in (await db.execute(existing_q)).all()
        )

        saved = 0
        for fact in facts[:5]:  # max 5 facts per message
            if not isinstance(fact, dict) or "fact" not in fact:
                continue
            fact_text = fact["fact"].strip()
            if not fact_text or fact_text.lower() in existing_contents:
                continue
            # Skip if very similar to existing
            if any(fact_text.lower() in ex or ex in fact_text.lower() for ex in existing_contents):
                continue

            memory_type = fact.get("type", "long_term")
            if memory_type not in ("long_term", "episodic", "short_term"):
                memory_type = "long_term"

            # Filter facts that look like client/equipment data — tool-recoverable, not worth storing
            if _MEMORY_BLOCK_RE.search(fact_text):
                logger.debug("memory: blocked low-value fact: %s", fact_text[:100])
                continue
            # Skip very short facts (<15 chars) — usually fragments
            if len(fact_text) < 15:
                continue

            # long_term facts are tenant-wide (available across all chats)
            # episodic/short_term are scoped to current chat
            entry = MemoryEntry(
                tenant_id=tenant_id,
                chat_id=None if memory_type == "long_term" else chat_id,
                memory_type=memory_type,
                content=fact_text,
                priority=1,
                is_pinned=False,
            )
            db.add(entry)
            await db.flush()
            await db.refresh(entry)
            existing_contents.add(fact_text.lower())
            saved += 1
            # Schedule background embedding so the entry is searchable next round
            try:
                import asyncio as _asyncio
                from app.services.memory.embedder import embed_memory_entry
                _asyncio.create_task(embed_memory_entry(entry.id))
            except Exception:
                logger.debug("memory: failed to schedule embed", exc_info=True)

        if saved:
            logger.debug(f"Auto-extracted {saved} memory fact(s) for tenant {tenant_id}")

    except json.JSONDecodeError:
        logger.debug("Memory extraction: no valid JSON in response")
    except Exception:
        logger.debug("Memory extraction failed (non-critical)", exc_info=True)


TOOL_SUMMARIZE_PROMPT = """Сожми результат вызова инструмента в краткое резюме (3-5 предложений).
Сохрани все ключевые данные: числа, имена, даты, суммы, идентификаторы.
Отбрось форматирование, повторы и маловажные детали.

Результат инструмента:
{tool_output}

Краткое резюме:"""


async def _summarize_old_tool_results(
    messages: list[dict],
    current_round: int,
    provider,
    model_name: str,
    correlation_id: str,
) -> tuple[int, int]:
    """
    Summarize large tool results from previous rounds (not the current one).
    Current round results stay full so LLM processes them completely.
    After this round, they'll be summarized in the next iteration.

    This preserves the essence of tool data while reducing token usage
    from O(rounds * data) to O(data + rounds * summary).
    """
    if current_round < 2:
        # First round — nothing to compress yet
        return (0, 0)

    # Find current round's tool block: comes after the last assistant with tool_calls.
    last_assistant_tool_idx = None
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "assistant" and messages[i].get("tool_calls"):
            last_assistant_tool_idx = i
            break
    if last_assistant_tool_idx is None:
        return (0, 0)

    total_prompt_tokens = 0
    total_completion_tokens = 0

    # Pre-build a single blob of all messages AFTER each candidate, so we can ask
    # "did the model reference any distinctive token from this tool result later?"
    # For efficiency we build per-i blob lazily — most chats are short.
    def _blob_after(idx: int) -> str:
        parts: list[str] = []
        for m in messages[idx + 1 :]:
            c = m.get("content")
            if isinstance(c, str):
                parts.append(c)
            elif isinstance(c, list):
                for part in c:
                    if isinstance(part, dict) and part.get("type") == "text":
                        parts.append(part.get("text", ""))
            for tc in m.get("tool_calls") or []:
                args = (tc.get("function") or {}).get("arguments")
                if isinstance(args, str):
                    parts.append(args)
        return "\n".join(parts)

    for i in range(last_assistant_tool_idx):
        msg = messages[i]
        if msg.get("role") != "tool":
            continue
        content = msg.get("content", "")
        if not isinstance(content, str):
            continue
        if len(content) <= TOOL_RESULT_COMPRESS_THRESHOLD:
            continue
        if content.startswith("[Резюме]") or content.startswith("[Сжато]") or "[" in content[:10] and "символов сжато" in content[:80]:
            continue  # already compressed

        # Pin check: did the model use any distinctive value from this result later?
        tokens = _extract_distinctive_tokens(content)
        blob = _blob_after(i)
        if _is_referenced_in(tokens, blob):
            logger.debug(
                f"[{correlation_id}] tool_result#{i} pinned ({len(content)} chars, "
                f"matched distinctive tokens)"
            )
            continue

        original_len = len(content)

        # Very long unpinned → LLM summary (worth the call)
        if original_len > TOOL_RESULT_LLM_SUMMARY_AT:
            try:
                prompt = TOOL_SUMMARIZE_PROMPT.format(tool_output=content[:8000])
                resp = await provider.chat_completion(
                    messages=[{"role": "user", "content": prompt}],
                    model=model_name,
                    temperature=0.1,
                    max_tokens=150,
                )
                prompt_tokens, completion_tokens = _usage_totals(resp)
                total_prompt_tokens += prompt_tokens
                total_completion_tokens += completion_tokens
                summary = resp.content.strip()[:600]
                msg["content"] = (
                    f"[Резюме] {summary}\n"
                    f"(Полные данные доступны через повторный вызов tool с тем же запросом.)"
                )
                logger.info(
                    f"[{correlation_id}] tool_result#{i} llm_summary: {original_len} -> {len(msg['content'])} chars"
                )
                continue
            except Exception as e:
                logger.warning(f"[{correlation_id}] tool_result#{i} llm_summary failed: {e}, falling back to deterministic")

        # Medium length OR LLM summary failed → deterministic head/tail
        msg["content"] = _deterministic_compress(content)
        logger.debug(
            f"[{correlation_id}] tool_result#{i} deterministic: {original_len} -> {len(msg['content'])} chars"
        )
    return total_prompt_tokens, total_completion_tokens


def _clamp_temperature(value: float | None) -> float:
    if value is None:
        return 0.3
    return max(0.0, min(float(value), MAX_SAFE_TEMPERATURE))


def _normalize_context_mode(value: str | None) -> str:
    if value in VALID_CONTEXT_MODES:
        return value
    return "summary_plus_recent"


def _build_history_dicts(messages: list[Message]) -> list[dict]:
    history: list[dict] = []
    for msg in messages:
        mapped = _message_to_history_dict(msg)
        if mapped:
            history.append(mapped)
    return history


def _message_to_history_dict(message: Message) -> dict | None:
    meta = message.metadata_json or {}
    if message.role != "assistant":
        return {"role": message.role, "content": message.content}

    if meta.get("history_exclude") or _looks_garbled_text(message.content):
        return _assistant_summary_dict(meta)

    return {"role": "assistant", "content": message.content}


def _assistant_summary_dict(meta: dict) -> dict | None:
    context_card = _compact_text(str(meta.get("context_card") or ""), max_chars=800)
    if context_card:
        return {"role": "assistant", "content": f"[Краткая карточка ответа]\n{context_card}"}

    response_summary = _compact_text(str(meta.get("response_summary") or ""), max_chars=500)
    if response_summary:
        return {"role": "assistant", "content": f"[Краткое содержание ответа]\n{response_summary}"}

    return None


def _compact_text(text: str | None, max_chars: int = 500) -> str | None:
    if not text:
        return None
    compact = re.sub(r"\s+", " ", text).strip()
    if not compact:
        return None
    if len(compact) > max_chars:
        return compact[:max_chars].rstrip() + "..."
    return compact


def _format_history_reference_block(history_dicts: list[dict]) -> str | None:
    if not history_dicts:
        return None
    lines = [
        "Ниже история диалога для справки.",
        "Это НЕ текущий запрос. Не отвечай на историю заново и не выполняй старые незавершённые задачи, если текущий запрос не просит продолжить их прямо.",
    ]
    for idx, msg in enumerate(history_dicts, start=1):
        role = msg.get("role")
        role_label = "USER" if role == "user" else "ASSISTANT"
        compact = _compact_text(msg.get("content") if isinstance(msg, dict) else None, max_chars=500)
        if compact:
            lines.append(f"{idx}. {role_label}: {compact}")
    return "\n".join(lines) if len(lines) > 2 else None


# Cap on inline-attachment payload (in characters of content_text). Files larger
# than this are inlined only as summary; full text stays reachable via
# search_attachment_<id> tool. ~3000 chars ≈ ~900 tokens.
INLINE_ATTACHMENT_FULL_TEXT_MAX_CHARS = 3000


def _build_current_attachments_block(
    attachments: list,  # list[MessageAttachment]
    tools_enabled: bool,
) -> str | None:
    """Compact in-context payload for files attached to THIS user message.
    Small textual files go in verbatim; bigger ones go as summary + a pointer
    to search_attachment_<short_id>."""
    if not attachments:
        return None
    parts: list[str] = ["[Прикреплено к этому сообщению]"]
    for att in attachments:
        fname = getattr(att, "filename", None) or "(без имени)"
        ftype = getattr(att, "file_type", "") or "file"
        fsize = getattr(att, "file_size_bytes", 0) or 0
        summary = (getattr(att, "summary", None) or "").strip()
        content_text = (getattr(att, "content_text", None) or "").strip()
        header = f"📎 {fname} ({ftype}, {fsize} байт)"
        parts.append(header)
        if summary:
            parts.append(f"Описание: {summary}")
        if content_text:
            if len(content_text) <= INLINE_ATTACHMENT_FULL_TEXT_MAX_CHARS:
                parts.append(f"Содержимое:\n{content_text}")
            else:
                aid_short = str(getattr(att, "id", ""))[:8]
                preview = content_text[:INLINE_ATTACHMENT_FULL_TEXT_MAX_CHARS].rstrip() + "..."
                parts.append(
                    f"Содержимое (первые {INLINE_ATTACHMENT_FULL_TEXT_MAX_CHARS} символов):\n{preview}"
                )
                if tools_enabled:
                    parts.append(
                        f"Полный текст файла доступен через tool "
                        f"`search_attachment_{aid_short}` — вызови с конкретным запросом."
                    )
    parts.append("[Прикреплено к этому сообщению — конец]")
    return "\n".join(parts)


def _format_current_user_request(user_content: str, *, for_tools: bool) -> str:
    if not for_tools:
        return user_content
    return (
        "[ТЕКУЩИЙ ЗАПРОС ПОЛЬЗОВАТЕЛЯ]\n"
        "Нужно ответить и при необходимости вызывать tools только по запросу ниже.\n"
        f"{user_content}"
    )


def _message_content_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text)
        return "\n".join(parts)
    return ""


def _build_prompt_layout(messages: list[dict], tool_defs: list[dict] | None, *, tool_mode: bool) -> dict:
    sections: list[dict] = []
    for idx, msg in enumerate(messages):
        role = msg.get("role")
        text = _message_content_text(msg.get("content"))
        if not text and role != "system":
            continue

        kind = "history_message"
        title = "История"
        if role == "system" and idx == 0:
            kind = "system_instructions"
            title = "System prompt и правила"
        elif role == "system":
            kind = "history_reference"
            title = "История как справка"
        elif role == "user" and idx == len(messages) - 1:
            kind = "current_request"
            title = "Текущий запрос"
        elif role == "assistant":
            title = "История: ответ ассистента"
        elif role == "user":
            title = "История: запрос пользователя"

        section = {
            "kind": kind,
            "title": title,
            "role": role,
            "chars": len(text),
            "est_tokens": _ct(text),
        }
        compact = _compact_text(text, max_chars=1600)
        if compact:
            section["content"] = compact
        sections.append(section)

    tool_names: list[str] = []
    if tool_defs:
        for tool in tool_defs:
            fn = tool.get("function") if isinstance(tool, dict) else None
            if isinstance(fn, dict):
                name = fn.get("name")
                if isinstance(name, str):
                    tool_names.append(name)

    return {
        "mode": "tool_partitioned" if tool_mode else "chat_transcript",
        "sections": sections,
        "tools": {
            "count": len(tool_names),
            "names": tool_names[:40],
        },
    }


def _build_tool_result_summary(tool_outputs: list[dict[str, str]]) -> str | None:
    if not tool_outputs:
        return None
    lines: list[str] = []
    for item in tool_outputs[:5]:
        tool_name = item["tool"]
        output = _compact_text(item["output"], max_chars=220) or "нет данных"
        lines.append(f"{tool_name}: {output}")
    return _compact_text(" | ".join(lines), max_chars=900)


def _build_attachment_summary(chat_attachments: list[MessageAttachment]) -> str | None:
    if not chat_attachments:
        return None
    parts: list[str] = []
    for att in chat_attachments[:5]:
        summary = _compact_text(att.summary or f"{att.filename} ({att.file_type})", max_chars=180)
        if summary:
            parts.append(f"{att.filename}: {summary}")
    return _compact_text(" | ".join(parts), max_chars=900) if parts else None


def _build_context_card(
    response_summary: str | None,
    tool_result_summary: str | None,
    attachment_summary: str | None,
) -> str | None:
    parts: list[str] = []
    if response_summary:
        parts.append(f"Ответ: {response_summary}")
    if tool_result_summary:
        parts.append(f"Инструменты: {tool_result_summary}")
    if attachment_summary:
        parts.append(f"Файлы: {attachment_summary}")
    return " || ".join(parts) if parts else None


def _looks_garbled_text(text: str | None) -> bool:
    if not text:
        return False
    compact = text.strip()
    if len(compact) < 160:
        return False

    tokens = re.findall(r"\S+", compact)
    if len(tokens) < 20:
        return False

    mixed_script = 0
    low_vowel = 0
    vowel_chars = set("aeiouyаеёиоуыэюяіїє")

    for token in tokens:
        clean = re.sub(r"[^A-Za-zА-Яа-яЁёЇїІіЄєҐґ0-9]", "", token)
        if len(clean) < 4:
            continue
        has_latin = bool(re.search(r"[A-Za-z]", clean))
        has_cyrillic = bool(re.search(r"[А-Яа-яЁёЇїІіЄєҐґ]", clean))
        if has_latin and has_cyrillic:
            mixed_script += 1
        if sum(ch.lower() in vowel_chars for ch in clean) == 0:
            low_vowel += 1

    suspicious_ratio = (mixed_script + low_vowel) / max(len(tokens), 1)
    return mixed_script >= 3 or suspicious_ratio > 0.35


def _detect_title_language(text: str | None) -> str | None:
    if not text:
        return None

    lowered = text.lower()
    if re.search(r"[іїєґ]", lowered):
        return "Ukrainian"
    if re.search(r"[а-яё]", lowered):
        return "Russian"
    if re.search(r"[a-z]", lowered):
        return "English"
    return None


async def _build_image_attachments_for_llM(
    chat_id: str,
    tenant_id: str,
    user_message_id: str | None,
    chat_attachments: list,
    db: AsyncSession,
    max_images: int = 4,
    max_bytes_each: int = 5 * 1024 * 1024,  # 5MB per image
) -> list[tuple[str, bytes, str]]:
    """
    Returns list of (filename, bytes, mime) for images to attach to the current
    user message. Priority: images attached to the current user message; if none,
    fall back to images of the most recent message that has any.
    Returns empty list if no images.
    """
    import base64  # noqa: F401 (used by callers indirectly)
    from app.services.storage import read_file

    image_atts = [a for a in chat_attachments if a.file_type == "image"]
    if not image_atts:
        return []

    # Prefer images attached to the current user message
    selected = []
    if user_message_id:
        try:
            uid = uuid.UUID(user_message_id)
            selected = [a for a in image_atts if a.message_id == uid]
        except (ValueError, AttributeError):
            selected = []

    # Fallback: most recent message with images
    if not selected:
        with_msg = [a for a in image_atts if a.message_id is not None]
        if with_msg:
            with_msg.sort(key=lambda a: a.created_at, reverse=True)
            latest_msg_id = with_msg[0].message_id
            selected = [a for a in with_msg if a.message_id == latest_msg_id]
        else:
            # No message_id linkage — just take the most recent images
            image_atts.sort(key=lambda a: a.created_at, reverse=True)
            selected = image_atts

    # Apply limits
    selected = selected[:max_images]

    out: list[tuple[str, bytes, str]] = []
    for att in selected:
        if att.file_size_bytes and att.file_size_bytes > max_bytes_each:
            logger.warning(f"Skipping image {att.filename}: size {att.file_size_bytes} > {max_bytes_each}")
            continue
        try:
            raw = await read_file(att.storage_path)
        except Exception as e:
            logger.warning(f"Failed to read image {att.storage_path}: {e}")
            continue
        mime = _guess_image_mime(att.filename)
        out.append((att.filename, raw, mime))
    return out


def _strip_image_data_from_messages(messages: list[dict]) -> list[dict]:
    """
    Replace base64 image payloads with short placeholders for safe logging.
    Handles both Ollama format (message['images']) and OpenAI format
    (message['content'] is a list with image_url parts).
    """
    out: list[dict] = []
    for m in messages:
        if not isinstance(m, dict):
            out.append(m)
            continue
        new_m = dict(m)
        # Ollama: images is a list of base64 strings
        if isinstance(new_m.get("images"), list) and new_m["images"]:
            new_m["images"] = [f"<image_b64 omitted, {len(s)} chars>" for s in new_m["images"]]
        # OpenAI: content can be a list with image_url parts
        if isinstance(new_m.get("content"), list):
            new_content = []
            for part in new_m["content"]:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    url = (part.get("image_url") or {}).get("url", "")
                    if url.startswith("data:"):
                        new_content.append({"type": "image_url", "image_url": {"url": f"<image data omitted, {len(url)} chars>"}})
                    else:
                        new_content.append(part)
                else:
                    new_content.append(part)
            new_m["content"] = new_content
        out.append(new_m)
    return out


def _guess_image_mime(filename: str) -> str:
    lower = filename.lower()
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if lower.endswith(".gif"):
        return "image/gif"
    if lower.endswith(".webp"):
        return "image/webp"
    if lower.endswith(".bmp"):
        return "image/bmp"
    return "application/octet-stream"


def _build_user_message_with_images(
    user_content: str,
    images: list[tuple[str, bytes, str]],
    provider_type: str,
) -> dict:
    """
    Build a multimodal user message based on provider format.
    Returns a single message dict.
    """
    import base64
    if not images:
        return {"role": "user", "content": user_content}

    if provider_type == "ollama":
        # Ollama format: content is plain text, images is list of base64 strings
        return {
            "role": "user",
            "content": user_content,
            "images": [base64.b64encode(b).decode("ascii") for _, b, _ in images],
        }

    # OpenAI-compatible format (works for OpenAI, Anthropic via OpenAI proxy, etc.)
    content_parts: list[dict] = [{"type": "text", "text": user_content}]
    for filename, raw, mime in images:
        b64 = base64.b64encode(raw).decode("ascii")
        content_parts.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{b64}"},
        })
    return {"role": "user", "content": content_parts}


def _query_needs_tools(user_content: str, chat_attachments: list) -> bool:
    """
    Decide if tools should be included in the LLM call.
    Default: True (include tools for any substantive question).
    Returns False only for clear chitchat (short greetings, thanks, confirmations)
    when no attachments are present.
    """
    text = user_content.lower().strip()

    # Attachments present → always include tools (file analysis usually needs them)
    if chat_attachments:
        return True

    # Empty / very short → likely chitchat
    if len(text) < 4:
        return False

    # Pure chitchat patterns (greetings, thanks, brief confirmations)
    chitchat_patterns = {
        "привет", "здравствуй", "здравствуйте", "добрый день", "добрый вечер",
        "доброе утро", "хай", "hi", "hello",
        "спасибо", "благодарю", "thanks", "thank you",
        "пока", "до свидания", "до встречи", "bye",
        "ок", "окей", "ok", "okay", "хорошо", "понятно", "понял", "ясно",
        "да", "нет", "ага", "угу", "yes", "no",
        "круто", "отлично", "супер", "класс",
    }
    # Strip trailing punctuation/emojis for comparison
    stripped = re.sub(r"[\s\.\!\?\,\;\:\)\(\-—…]+$", "", text)
    stripped = re.sub(r"^[\s\.\!\?\,\;\:\)\(\-—…]+", "", stripped)
    if stripped in chitchat_patterns:
        return False

    # Short message (< 12 chars) that exactly matches a chitchat prefix → chitchat
    if len(text) < 12:
        for pat in chitchat_patterns:
            if stripped.startswith(pat) and len(stripped) <= len(pat) + 3:
                return False

    # Default: include tools for any substantive question
    return True


MAX_TOOLS_PER_REQUEST = 20    # ≤ → send all; > → use semantic selection
TOOL_KEYWORD_THRESHOLD = 80   # use keyword matching up to this; semantic above
TOOL_SEMANTIC_TOPK = 18       # how many tools to pull from semantic search
LOCAL_QWEN_TOOL_BUDGET = 8
DEFAULT_TOOL_BUDGET = 12
TOOL_ROUTE_PON = "pon_address_workflow"


def _tool_budget_for_model(model_name: str | None) -> int:
    lowered = (model_name or "").lower()
    if "qwen2.5" in lowered or "qwen2_5" in lowered:
        return LOCAL_QWEN_TOOL_BUDGET
    return DEFAULT_TOOL_BUDGET


def _detect_tool_route(user_message: str) -> str | None:
    text = (user_message or "").lower()
    if not text:
        return None
    pon_keywords = (
        "pon", "gpon", "epon", "xpon",
        "делител", "сплиттер", "splitter",
        "хвост", "свободн", "бюджет", "dbm", "сигнал", "olt", "onu",
    )
    if any(token in text for token in pon_keywords):
        return TOOL_ROUTE_PON
    return None


def _route_tool_names(route_name: str | None) -> list[str]:
    if route_name == TOOL_ROUTE_PON:
        return [
            "pon_search",
            "pon_tree",
            "pon_path",
            "pon_olts",
            "search_addresses",
            "pon_nearby",
            "geocode_address",
        ]
    return []


def _tool_route_system_hint(route_name: str | None, available_tool_names: set[str]) -> str | None:
    if route_name != TOOL_ROUTE_PON:
        return None
    has_pon_search = "pon_search" in available_tool_names
    has_pon_tree = "pon_tree" in available_tool_names
    has_pon_path = "pon_path" in available_tool_names
    ordered_steps: list[str] = []
    if has_pon_search:
        ordered_steps.append("1. СНАЧАЛА ищи объект по адресу/комментарию через pon_search.")
    if has_pon_tree:
        ordered_steps.append("2. Если найден делитель/сплиттер/OLT/клиент с ancestors — используй pon_tree для проверки хвостов, клиентов и сигнала.")
    if has_pon_path:
        ordered_steps.append("3. Если нужен путь до OLT или уточнение родителя — используй pon_path.")
    ordered_steps.append("4. Не используй geocode_address или pon_nearby, пока pon_search не вернул пусто или неоднозначно.")
    ordered_steps.append("5. Не меняй адрес пользователя на соседний адрес из истории без нового tool-результата.")
    return "PON workflow:\n" + "\n".join(ordered_steps)


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

    current_route = _detect_tool_route(current)
    prior_route = _detect_tool_route(prior)
    if current_route or prior_route:
        return current_route is not None and current_route == prior_route

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


async def _select_relevant_tools(
    all_tools: list,
    user_message: str,
    provider,
    model_name: str,
    *,
    embedding_model: str | None = None,
    db = None,
    tenant_id: str | None = None,
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

    budget = min(MAX_TOOLS_PER_REQUEST, _tool_budget_for_model(model_name))
    route_name = _detect_tool_route(user_message)

    # Tier 1 — small set, send everything only when the budget allows it and
    # there is no domain route that needs to narrow the tool space.
    if len(all_tools) <= budget and route_name is None:
        return all_tools

    pinned = [t for t in all_tools if getattr(t, "is_pinned", False)]
    pinned_ids = {t.id for t in pinned}
    rest = [t for t in all_tools if t.id not in pinned_ids]

    selected: list = []
    selection_method = ""

    route_tool_names = _route_tool_names(route_name)
    if route_tool_names:
        route_map = {
            ((t.config_json or {}).get("function") or {}).get("name", t.name): t
            for t in rest
        }
        selected = [route_map[name] for name in route_tool_names if name in route_map]
        selection_method = f"route:{route_name}"

    # Tier 2 — semantic search when embeddings available (default for >MAX)
    embeddable = [t for t in rest if getattr(t, "embedding", None) is not None]
    has_enough_embeddings = embedding_model and db is not None and tenant_id and len(embeddable) >= len(rest) // 2
    if not selected and has_enough_embeddings:
        try:
            from app.services.tools.embedder import search_tools
            semantic_results = await search_tools(
                tenant_id=str(tenant_id),
                query=user_message,
                db=db,
                embedding_model=embedding_model,
                top_k=TOOL_SEMANTIC_TOPK,
            )
            if semantic_results:
                # Include semantic hits PLUS any non-embedded tools (don't silently drop them)
                semantic_ids = {t.id for t in semantic_results}
                non_embedded = [t for t in rest if getattr(t, "embedding", None) is None and t.id not in semantic_ids]
                selected = [*semantic_results, *non_embedded]
                selection_method = "semantic"
        except Exception:
            logger.exception("semantic tool selection failed; falling back to keyword")

    # Tier 3 — keyword fallback (also used for small tenants without embeddings)
    if not selected and len(rest) <= TOOL_KEYWORD_THRESHOLD:
        try:
            selected = _keyword_match_tools(rest, user_message)
            selection_method = "keyword"
        except Exception:
            logger.exception("keyword tool selection failed")

    # Tier 4 — last resort, LLM picks from name+description list
    if not selected:
        try:
            selected = await _llm_select_tools(rest, user_message, provider, model_name)
            selection_method = "llm-pick"
        except Exception:
            selected = []
            selection_method = "fallback-empty"

    # Pinned tools are "system-essentials" (memory/artifacts/RAG helpers).
    # They go in ABOVE the budget — budget only constrains the non-pinned
    # semantic/route/keyword selection. Otherwise pinned starves out the
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


def _keyword_match_tools(all_tools: list, user_message: str) -> list:
    """Score tools by keyword overlap with user message."""
    msg_lower = user_message.lower()
    msg_words = set(msg_lower.split())

    scored = []
    for tool in all_tools:
        score = 0
        name = (tool.name or "").lower()
        desc = (tool.description or "").lower()
        tags = " ".join(_tool_capability_tags(tool)).lower()
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


async def _llm_select_tools(
    all_tools: list,
    user_message: str,
    provider,
    model_name: str,
) -> list:
    """Use LLM to select relevant tools from a large set."""
    tools_summary = "\n".join(
        f"- {t.name} [{', '.join(_tool_capability_tags(t)) or 'no-tags'}]: {(t.description or 'нет описания')[:100]}"
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
        return _keyword_match_tools(all_tools, user_message)

    name_set = set(str(n).lower().strip() for n in selected_names)
    selected = [t for t in all_tools if t.name.lower().strip() in name_set]

    logger.debug(f"LLM tool selection: {len(selected)}/{len(all_tools)} tools selected")
    return selected[:MAX_TOOLS_PER_REQUEST]


def _tool_capability_tags(tool) -> list[str]:
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


async def _load_allowed_tool_ids(
    db: AsyncSession,
    tenant_id: str,
    api_key_id: str | None,
) -> set[str] | None:
    if not api_key_id:
        return None

    api_key = (
        await db.execute(
            select(TenantApiKey).where(
                TenantApiKey.id == api_key_id,
                TenantApiKey.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if not api_key:
        return None

    if api_key.allowed_tool_ids is not None:
        return {str(tool_id) for tool_id in api_key.allowed_tool_ids}

    if api_key.group_id:
        group = (
            await db.execute(
                select(TenantApiKeyGroup).where(
                    TenantApiKeyGroup.id == api_key.group_id,
                    TenantApiKeyGroup.tenant_id == tenant_id,
                )
            )
        ).scalar_one_or_none()
        if group and group.allowed_tool_ids is not None:
            return {str(tool_id) for tool_id in group.allowed_tool_ids}

    return None


def _public_tool_def(tool_config: dict) -> dict:
    """Strip runtime-only metadata before sending the tool definition to the provider."""
    if not isinstance(tool_config, dict):
        return tool_config
    public: dict = {}
    if "type" in tool_config:
        public["type"] = tool_config["type"]
    if isinstance(tool_config.get("function"), dict):
        public["function"] = json.loads(json.dumps(tool_config["function"]))
        _augment_public_tool_definition(public["function"], tool_config)
    return public or tool_config


def _augment_public_tool_definition(function_def: dict, tool_config: dict) -> None:
    """Add backend-aware guidance to tool definitions before sending them to the LLM."""
    runtime = tool_config.get("x_backend_config")
    if not isinstance(runtime, dict):
        return

    handler = str(runtime.get("handler") or "").strip().lower()
    if handler == "search_records":
        _augment_search_records_tool_definition(function_def, runtime)


def _augment_search_records_tool_definition(function_def: dict, runtime: dict) -> None:
    params = function_def.get("parameters")
    if not isinstance(params, dict):
        return

    properties = params.get("properties")
    if not isinstance(properties, dict):
        return

    filter_fields = runtime.get("filter_fields")
    if not isinstance(filter_fields, dict) or not filter_fields:
        return

    filters_prop = properties.get("filters")
    if isinstance(filters_prop, dict):
        filters_prop = json.loads(json.dumps(filters_prop))
        filter_properties = filters_prop.get("properties")
        if not isinstance(filter_properties, dict):
            filter_properties = {}
            filters_prop["properties"] = filter_properties

        filter_lines: list[str] = []
        for alias, field_cfg in filter_fields.items():
            if not isinstance(field_cfg, dict):
                continue
            alias_name = str(alias).strip()
            if not alias_name:
                continue
            mode = str(field_cfg.get("mode") or "exact").strip().lower()
            label = str(field_cfg.get("description") or "").strip() or _humanize_filter_alias(alias_name, mode)
            filter_lines.append(f"{alias_name}: {label}")
            existing_prop = filter_properties.get(alias_name)
            if not isinstance(existing_prop, dict):
                existing_prop = {"type": "string"}
            existing_prop["description"] = label
            filter_properties[alias_name] = existing_prop

        if filter_lines:
            filters_prop["description"] = (
                "Предпочтительный способ точного поиска по известным полям. "
                "Если пользователь явно называет поле, используй filters вместо query. "
                "Доступные поля: " + "; ".join(filter_lines)
            )
            filters_prop["additionalProperties"] = False
            properties["filters"] = filters_prop

    query_prop = properties.get("query")
    if isinstance(query_prop, dict):
        search_columns = runtime.get("search_columns")
        columns_hint = ""
        if isinstance(search_columns, list) and search_columns:
            preview = ", ".join(str(col) for col in search_columns[:6])
            columns_hint = f" по columns: {preview}"
        query_prop["description"] = (
            "Свободный текстовый fallback-поиск. Используй query только когда запрос нельзя выразить через filters; "
            "не смешивай query с filters без необходимости, потому что это медленнее"
            f"{columns_hint}."
        )

    known_fields = ", ".join(sorted(str(alias) for alias in filter_fields.keys()))
    desc = str(function_def.get("description") or "").strip()
    guidance = (
        " Используй filters для явных полей "
        f"({known_fields}); query оставляй только для неструктурированного поиска."
    )
    if guidance.strip() not in desc:
        function_def["description"] = (desc + guidance).strip()


def _humanize_filter_alias(alias: str, mode: str) -> str:
    pretty_names = {
        "street": "Название улицы или её часть",
        "house": "Номер дома",
        "apart": "Квартира или её часть",
        "litera": "Литера дома",
        "client_name": "Имя клиента или его часть",
        "name": "Имя или его часть",
        "phone": "Телефон или его часть",
        "id": "Точный идентификатор",
        "client_id": "Точный ID клиента",
        "contract_number": "Номер договора или его часть",
        "dogovor_num": "Номер договора или его часть",
    }
    if alias in pretty_names:
        return pretty_names[alias]
    if mode == "contains":
        return f"Значение поля {alias} или его часть"
    if mode in {"eq", "exact"}:
        return f"Точное значение поля {alias}"
    if mode == "starts_with":
        return f"Начало значения поля {alias}"
    if mode == "lte":
        return f"Верхняя граница для поля {alias}"
    if mode == "gte":
        return f"Нижняя граница для поля {alias}"
    return f"Значение поля {alias}"
