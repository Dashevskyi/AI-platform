import json
import logging
import time
import uuid

from sqlalchemy import select
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
    TenantShellConfig,
    TenantTool,
)
from app.providers.factory import get_provider
from app.core.security import decrypt_value, redact_for_log
from app.services.tools.executor import execute_tool
from app.services.kb.embedder import search_kb_chunks
from app.services.llm.model_resolver import resolve_model

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 5  # prevent infinite tool-call loops


async def chat_completion(
    tenant_id: str,
    chat_id: str,
    user_content: str,
    db: AsyncSession,
    user_message_id: str | None = None,
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

    # Exclude current user message from history (it will be appended explicitly)
    if user_message_id and recent_msgs:
        recent_msgs = [m for m in recent_msgs if str(m.id) != user_message_id]

    # 3. Memory
    memory_entries: list = []
    if config.memory_enabled:
        mem_q = (
            select(MemoryEntry)
            .where(
                MemoryEntry.tenant_id == tenant_id,
                MemoryEntry.deleted_at.is_(None),
                (MemoryEntry.chat_id == chat_id) | (MemoryEntry.chat_id.is_(None)),
            )
            .order_by(MemoryEntry.priority.desc(), MemoryEntry.is_pinned.desc())
            .limit(10)
        )
        memory_entries = list((await db.execute(mem_q)).scalars().all())

    # 4. Resolve model via catalog (or fallback to shell_config)
    resolved = await resolve_model(tenant_id, user_content, db, config)
    provider = resolved.provider
    model_name = resolved.model_name
    logger.info(f"[{correlation_id}] Model resolved: {model_name} (source={resolved.source}, provider={resolved.provider_type})")

    # 5. KB — semantic search via embeddings (always use Ollama for local embedding model)
    kb_chunks: list = []
    if config.knowledge_base_enabled and config.embedding_model_name:
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

    # 6. Tools — load all, then select relevant
    tools_q = select(TenantTool).where(
        TenantTool.tenant_id == tenant_id,
        TenantTool.is_active == True,  # noqa: E712
        TenantTool.deleted_at.is_(None),
    )
    all_tools = list((await db.execute(tools_q)).scalars().all())
    tools = await _select_relevant_tools(all_tools, user_content, provider, model_name)

    # 6b. Load chat attachments (processed) — register dynamic search tools
    attachment_tool_defs: list[dict] = []
    attachment_map: dict[str, str] = {}  # tool_name -> attachment_id
    attachments_q = select(MessageAttachment).where(
        MessageAttachment.chat_id == chat_id,
        MessageAttachment.tenant_id == tenant_id,
        MessageAttachment.processing_status == "done",
    )
    chat_attachments = list((await db.execute(attachments_q)).scalars().all())
    if chat_attachments:
        from app.services.attachments.tool import build_attachment_tool_def
        for att in chat_attachments:
            tool_def = build_attachment_tool_def(str(att.id), att.filename, att.summary)
            attachment_tool_defs.append(tool_def)
            tool_name = tool_def["function"]["name"]
            attachment_map[tool_name] = str(att.id)

    # 7. Build messages
    system_parts: list[str] = []
    if config.system_prompt:
        system_parts.append(config.system_prompt)
    if config.rules_text:
        system_parts.append(f"Rules:\n{config.rules_text}")
    if memory_entries:
        mem_text = "\n".join(f"- [{m.memory_type}] {m.content}" for m in memory_entries)
        system_parts.append(f"Memory:\n{mem_text}")
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
        system_parts.append(
            "Knowledge Base (relevant excerpts):\n---\n"
            + "\n---\n".join(kb_parts)
        )

    # Add attachment summaries to system prompt
    if chat_attachments:
        att_lines = []
        for att in chat_attachments:
            att_lines.append(f"- {att.filename} ({att.file_type}, {att.file_size_bytes} байт): {att.summary or 'нет описания'}")
        system_parts.append(
            "Приложенные файлы (используй инструменты search_attachment_* для поиска по содержимому):\n"
            + "\n".join(att_lines)
        )

    messages: list[dict] = []
    if system_parts:
        messages.append({"role": "system", "content": "\n\n".join(system_parts)})
    for m in recent_msgs:
        messages.append({"role": m.role, "content": m.content})
    # Append current user message explicitly (excluded from DB query to avoid duplication)
    messages.append({"role": "user", "content": user_content})

    # Merge tenant tools + attachment search tools
    tool_defs = [t.config_json for t in tools if t.config_json] if tools else []
    tool_defs = tool_defs + attachment_tool_defs
    if not tool_defs:
        tool_defs = None

    # 8. Call provider
    total_prompt_tokens = 0
    total_completion_tokens = 0
    tool_calls_total = 0

    start = time.time()
    resp = None
    error_text = None
    status = "success"

    try:
        # Initial LLM call
        resp = await provider.chat_completion(
            messages=messages,
            model=model_name,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            tools=tool_defs,
        )

        if resp.prompt_tokens:
            total_prompt_tokens += resp.prompt_tokens
        if resp.completion_tokens:
            total_completion_tokens += resp.completion_tokens

        # Tool execution loop
        round_num = 0
        while resp.tool_calls and round_num < MAX_TOOL_ROUNDS:
            round_num += 1
            tool_calls_total += len(resp.tool_calls)

            logger.info(f"[{correlation_id}] Tool round {round_num}: {len(resp.tool_calls)} call(s)")

            # Add assistant message with tool_calls to conversation
            assistant_tool_msg = {"role": "assistant", "content": resp.content or ""}
            if resolved.provider_type == "ollama":
                # Ollama format: tool_calls in message
                assistant_tool_msg["tool_calls"] = resp.tool_calls
            else:
                # OpenAI format
                assistant_tool_msg["tool_calls"] = resp.tool_calls
            messages.append(assistant_tool_msg)

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

                logger.info(f"[{correlation_id}] Executing tool: {func_name}({func_args})")

                # Check if this is an attachment search tool
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
                    result = await execute_tool(func_name, func_args)
                    tool_output = result.output if result.success else f"Ошибка: {result.error}"

                logger.info(f"[{correlation_id}] Tool result: {tool_output[:200]}")

                # Add tool result to messages
                if resolved.provider_type == "ollama":
                    messages.append({
                        "role": "tool",
                        "content": tool_output,
                    })
                else:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": tool_output,
                    })

            # Call LLM again with tool results
            resp = await provider.chat_completion(
                messages=messages,
                model=model_name,
                temperature=config.temperature,
                max_tokens=config.max_tokens,
                tools=tool_defs,
            )

            if resp.prompt_tokens:
                total_prompt_tokens += resp.prompt_tokens
            if resp.completion_tokens:
                total_completion_tokens += resp.completion_tokens

        latency = (time.time() - start) * 1000

    except Exception as e:
        latency = (time.time() - start) * 1000
        status = "error"
        error_text = str(e)

    # 8. Save log
    raw_req = redact_for_log({
        "messages": messages,
        "model": model_name,
        "temperature": config.temperature,
        "tools": tool_defs,
    })
    raw_resp = redact_for_log(resp.raw_response) if resp and resp.raw_response else None
    req_bytes = len(json.dumps(messages).encode()) if messages else None
    resp_bytes = len(resp.content.encode()) if resp else None

    total_tokens = (total_prompt_tokens + total_completion_tokens) if (total_prompt_tokens or total_completion_tokens) else None

    # Build normalized with tool execution details and token breakdown
    system_content = ""
    history_content = ""
    for m in messages:
        if m["role"] == "system":
            system_content = m.get("content", "")
        elif m["role"] in ("user", "assistant"):
            history_content += m.get("content", "") + " "

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

    logger.info(f"[{correlation_id}] Context breakdown: "
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
    }
    norm_resp: dict = {"content_length": len(resp.content) if resp else 0}
    if tool_calls_total > 0:
        # Include tool call details in normalized response
        tool_log = []
        for m in messages:
            if m.get("role") == "tool":
                tool_log.append({"role": "tool", "content": m.get("content", "")[:500]})
            elif m.get("role") == "assistant" and m.get("tool_calls"):
                calls = []
                for tc in m["tool_calls"]:
                    func = tc.get("function", tc)
                    calls.append({"name": func.get("name"), "arguments": func.get("arguments")})
                tool_log.append({"role": "assistant_tool_calls", "calls": calls})
        norm_resp["tool_execution"] = tool_log

    log = LLMRequestLog(
        tenant_id=tenant_id,
        chat_id=chat_id,
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
    )
    db.add(log)

    if not resp:
        raise ValueError(f"LLM call failed: {error_text}")

    # 9. Auto-extract memory facts
    if config.memory_enabled and resp:
        await _extract_memory(provider, model_name, tenant_id, chat_id, user_content, resp.content, db)

    # 10. Auto-summary
    chat = (await db.execute(select(Chat).where(Chat.id == chat_id))).scalar_one_or_none()
    if chat and (not chat.title or not chat.description) and user_content:
        try:
            summary_model = (getattr(config, "summary_model_name", None) or config.model_name).strip()
            summary = await provider.summarize(
                f"User: {user_content}\nAssistant: {resp.content}",
                summary_model,
            )
            summary = summary[:200]
            if not chat.title:
                chat.title = summary
            if not chat.description:
                chat.description = summary
        except Exception:
            pass

    # 11. Return response
    return {
        "content": resp.content,
        "prompt_tokens": total_prompt_tokens or resp.prompt_tokens,
        "completion_tokens": total_completion_tokens or resp.completion_tokens,
        "total_tokens": total_tokens,
        "latency_ms": latency,
        "finish_reason": resp.finish_reason,
        "correlation_id": correlation_id,
        "tool_calls": resp.tool_calls,
        "tool_calls_count": tool_calls_total,
    }


MEMORY_EXTRACTION_PROMPT = """Проанализируй диалог и извлеки важные факты о пользователе или контексте.
Верни ТОЛЬКО JSON-массив фактов. Каждый факт — объект с полями:
- "fact": краткая формулировка факта (1 предложение)
- "type": "long_term" (постоянный факт о пользователе) или "episodic" (факт о текущей ситуации)

Извлекай ТОЛЬКО если пользователь явно сообщил: имя, контактные данные, адрес, номер договора, тариф, оборудование, проблему.
Если новых фактов нет — верни пустой массив [].
НЕ придумывай факты. НЕ извлекай общеизвестную информацию.

Диалог:
User: {user_message}
Assistant: {assistant_message}

JSON:"""


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

            entry = MemoryEntry(
                tenant_id=tenant_id,
                chat_id=chat_id,
                memory_type=memory_type,
                content=fact_text,
                priority=1,
                is_pinned=False,
            )
            db.add(entry)
            existing_contents.add(fact_text.lower())
            saved += 1

        if saved:
            logger.info(f"Auto-extracted {saved} memory fact(s) for tenant {tenant_id}")

    except json.JSONDecodeError:
        logger.debug("Memory extraction: no valid JSON in response")
    except Exception:
        logger.debug("Memory extraction failed (non-critical)", exc_info=True)


MAX_TOOLS_PER_REQUEST = 10
TOOL_SELECTION_THRESHOLD = 20  # use keyword matching below this, LLM above


async def _select_relevant_tools(
    all_tools: list,
    user_message: str,
    provider,
    model_name: str,
) -> list:
    """
    Select relevant tools for the user message.
    - If <= MAX_TOOLS_PER_REQUEST tools exist — return all (no filtering needed)
    - If <= TOOL_SELECTION_THRESHOLD — use fast keyword matching
    - If > TOOL_SELECTION_THRESHOLD — use LLM to pick relevant tools
    """
    if not all_tools:
        return []

    if len(all_tools) <= MAX_TOOLS_PER_REQUEST:
        return all_tools

    # Fast keyword matching for moderate number of tools
    if len(all_tools) <= TOOL_SELECTION_THRESHOLD:
        return _keyword_match_tools(all_tools, user_message)

    # LLM-based selection for large number of tools
    try:
        return await _llm_select_tools(all_tools, user_message, provider, model_name)
    except Exception:
        logger.warning("LLM tool selection failed, falling back to keyword matching")
        return _keyword_match_tools(all_tools, user_message)


def _keyword_match_tools(all_tools: list, user_message: str) -> list:
    """Score tools by keyword overlap with user message."""
    msg_lower = user_message.lower()
    msg_words = set(msg_lower.split())

    scored = []
    for tool in all_tools:
        score = 0
        name = (tool.name or "").lower()
        desc = (tool.description or "").lower()
        # Name match is strong signal
        if name in msg_lower:
            score += 10
        # Word overlap
        tool_words = set(name.split("_")) | set(name.split("-")) | set(desc.split())
        overlap = msg_words & tool_words
        score += len(overlap) * 2
        # Partial substring match in description
        for word in msg_words:
            if len(word) > 3 and word in desc:
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
        f"- {t.name}: {(t.description or 'нет описания')[:100]}"
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

    logger.info(f"LLM tool selection: {len(selected)}/{len(all_tools)} tools selected")
    return selected[:MAX_TOOLS_PER_REQUEST]
