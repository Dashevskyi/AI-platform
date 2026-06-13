import asyncio
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass

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


def _content_to_text(c) -> str:
    """Flatten a message content (str or list-of-parts) to plain text."""
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(p.get("text", "") for p in c if isinstance(p, dict) and p.get("type") == "text")
    return ""


async def _collect_capture_artifacts(capture_tasks_by_round: dict, round_breakdown: list, correlation_id: str) -> None:
    """Briefly await the background artifact-capture tasks and attribute the new
    artifact_ids to their round in round_breakdown[].artifacts_captured. Tightly
    time-boxed (5s) so a hung embed never blocks the response. Best-effort."""
    if not capture_tasks_by_round:
        return
    try:
        for r_num, items in capture_tasks_by_round.items():
            tasks = [t for _, t in items]
            done = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=5.0)
            arts: list[dict] = []
            for (t_name, _t), result in zip(items, done):
                aid = str(result) if (not isinstance(result, Exception) and result is not None) else None
                arts.append({"tool_name": t_name, "artifact_id": aid})
            for r_entry in round_breakdown:
                if r_entry.get("round") == r_num:
                    r_entry["artifacts_captured"] = arts
                    break
    except asyncio.TimeoutError:
        logger.warning("[%s] artifact-capture tasks timed out (5s); debug.artifacts_captured may be incomplete", correlation_id)
    except Exception:
        logger.exception("[%s] failed to collect artifact-capture results (non-fatal)", correlation_id)


def _build_normalized_response(resp, messages: list[dict], tool_calls_total: int) -> dict:
    """Normalized response for the request log: content length plus, when tools
    ran, a compact tool-execution trail. JSON tool results are parsed and large
    tables truncated to TOOL_LOG_MAX_ROWS (real count preserved) so the UI can
    render them without truncation breaking JSON.parse. Pure (no DB)."""
    norm: dict = {"content_length": len(resp.content) if resp else 0}
    if tool_calls_total <= 0:
        return norm

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

    def _store_tool_content(raw):
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
    norm["tool_execution"] = tool_log
    return norm


def _snapshot_messages(msgs: list[dict]) -> list[dict]:
    """Compact per-message summary (role, chars, est_tokens, brief) for the
    debug trace, so the UI can show what was sent into each round. Pure."""
    out: list[dict] = []
    for m in msgs:
        role = m.get("role", "?")
        raw = m.get("content")
        if isinstance(raw, list):
            # vision messages: list of parts
            text = "\n".join(
                p.get("text") or f"<{p.get('type', 'part')}>" for p in raw if isinstance(p, dict)
            )
        else:
            text = str(raw or "")
        brief = text[:160].replace("\n", " ⏎ ")
        if len(text) > 160:
            brief += " …"
        entry: dict = {"role": role, "chars": len(text), "est_tokens": _ct(text), "brief": brief}
        tcs = m.get("tool_calls")
        if isinstance(tcs, list) and tcs:
            names = []
            for tc in tcs:
                fn = (tc or {}).get("function") or {}
                n = fn.get("name") if isinstance(fn, dict) else None
                if isinstance(n, str):
                    names.append(n)
            if names:
                entry["tool_calls"] = names
        if role == "tool":
            tcid = m.get("tool_call_id")
            if tcid:
                entry["tool_call_id"] = str(tcid)[:50]
        out.append(entry)
    return out


def _compute_context_breakdown(messages, config, memory_entries, kb_chunks, tool_defs, correlation_id: str) -> dict:
    """Per-section char/token telemetry for the assembled prompt (tiktoken).
    Telemetry only — exact prompt tokens come from the provider. Pure (no DB)."""
    history_content = ""
    for m in messages:
        if m["role"] in ("user", "assistant"):
            history_content += _content_to_text(m.get("content", "")) + " "

    system_prompt_text = config.system_prompt or ""
    rules_text = config.rules_text or ""
    memory_text = "".join(m.content or "" for m in memory_entries)
    kb_text = "".join(c.content or "" for c in kb_chunks)
    tools_text = json.dumps(tool_defs) if tool_defs else ""

    breakdown = {
        "system_prompt": {"chars": len(system_prompt_text), "est_tokens": _ct(system_prompt_text)},
        "rules": {"chars": len(rules_text), "est_tokens": _ct(rules_text)},
        "memory": {"chars": len(memory_text), "entries": len(memory_entries), "est_tokens": _ct(memory_text)},
        "kb": {"chars": len(kb_text), "chunks": len(kb_chunks), "est_tokens": _ct(kb_text)},
        "history": {"chars": len(history_content), "messages": len([m for m in messages if m["role"] != "system"]), "est_tokens": _ct(history_content)},
        "tools": {"chars": len(tools_text), "count": len(tool_defs) if tool_defs else 0, "est_tokens": _ct(tools_text)},
    }
    breakdown["total_est_tokens"] = sum(v["est_tokens"] for v in breakdown.values())

    logger.debug(f"[{correlation_id}] Context breakdown: "
                f"system={breakdown['system_prompt']['est_tokens']}t, "
                f"rules={breakdown['rules']['est_tokens']}t, "
                f"memory={breakdown['memory']['est_tokens']}t({len(memory_entries)}), "
                f"kb={breakdown['kb']['est_tokens']}t({len(kb_chunks)}chunks), "
                f"history={breakdown['history']['est_tokens']}t({breakdown['history']['messages']}msgs), "
                f"tools={breakdown['tools']['est_tokens']}t({breakdown['tools']['count']}), "
                f"TOTAL≈{breakdown['total_est_tokens']}t")
    return breakdown


@dataclass
class ToolExecCtx:
    """Shared state + dependencies for executing tool calls within the loop.

    The mutable structures (messages, tool_outputs, tool_config_map, tool_defs,
    capture_tasks_by_round, debug_tool_calls) are held by reference and mutated
    in place — the orchestrator reads them back after the loop. Built once before
    the loop; `round_num` varies and is passed per call."""
    messages: list
    tool_outputs: list
    tool_config_map: dict
    tool_defs: list | None
    capture_tasks_by_round: dict
    debug_tool_calls: list
    allowed_tool_names: set
    attachment_map: dict
    all_allowed_tools_for_tenant: dict
    provider: object
    db: object
    config: object
    tenant_id: object
    chat_id: object
    user_message_id: object
    correlation_id: str
    emit: object  # async callable(event_type: str, payload: dict)
    # Provider-round deps (used by _run_provider_round).
    model_name: str = ""
    user_content: str = ""
    chunk_cb: object = None
    current_round_ref: dict = None
    round_breakdown: list = None
    tool_routing_temperature: float = 0.0
    effective_temperature: float = 0.3
    # Auto tool-limit bookkeeping (per request). name → call count; failures
    # is a 1-element list used as a mutable int box.
    tool_call_counts: dict = None
    failed_calls: list = None


async def _run_provider_round(ctx: ToolExecCtx, round_num: int, *, voice_mode: bool = False):
    """One LLM call within a turn (round 0 or a tool round) plus its telemetry:
    emits provider_call_start/done + reasoning, records the round_breakdown entry,
    and returns the response. Token accumulation stays with the caller."""
    await ctx.emit("provider_call_start", {"round": round_num, "model": ctx.model_name})
    t0 = time.time()
    ctx.current_round_ref["round"] = round_num
    resp = await ctx.provider.chat_completion(
        messages=ctx.messages,
        model=ctx.model_name,
        temperature=(ctx.tool_routing_temperature if ctx.tool_defs else ctx.effective_temperature),
        max_tokens=ctx.config.max_tokens,
        tools=ctx.tool_defs,
        on_chunk=ctx.chunk_cb,
        extra_body=_resolve_thinking_kwargs(
            getattr(ctx.config, "enable_thinking", "on"), ctx.user_content, bool(ctx.tool_defs),
            voice_mode=voice_mode,
        ),
    )
    latency_ms = int((time.time() - t0) * 1000)
    pt = int(resp.prompt_tokens or 0)
    ct = int(resp.completion_tokens or 0)
    ctx.round_breakdown.append({
        "round": round_num,
        "prompt_tokens": pt,
        "completion_tokens": ct,
        "latency_ms": latency_ms,
        "has_tool_calls": bool(resp.tool_calls),
        "tool_calls_count": len(resp.tool_calls or []) if resp.tool_calls else 0,
        "messages_snapshot": _snapshot_messages(ctx.messages),
        "response_content_chars": len(resp.content or ""),
        "response_reasoning_chars": len(getattr(resp, "reasoning", "") or ""),
    })
    await ctx.emit("provider_call_done", {
        "round": round_num,
        "latency_ms": latency_ms,
        "has_tool_calls": bool(resp.tool_calls),
        "content_chars": len(resp.content or ""),
        "reasoning_chars": len(resp.reasoning or ""),
        "prompt_tokens": pt,
        "completion_tokens": ct,
    })
    if resp.reasoning:
        await ctx.emit("reasoning", {"round": round_num, "text": resp.reasoning})
    return resp


async def _execute_tool_call(ctx: ToolExecCtx, tc, round_num: int) -> None:
    """Execute one tool_call: dispatch (regular tool or attachment search), emit
    lifecycle events, record debug + outputs, schedule artifact capture, and
    append the result turn to messages. Mutates ctx in place; a blocked or
    unparseable call is skipped."""
    parsed = _parse_tool_call(tc)
    if parsed is None:
        return
    tool_call_id, func_name, func_args = parsed

    logger.debug(f"[{ctx.correlation_id}] Executing tool: {func_name}({func_args})")

    if func_name not in ctx.allowed_tool_names:
        logger.warning(f"[{ctx.correlation_id}] Blocked tool call not allowed for tenant/chat: {func_name}")
        tool_output = f"Ошибка: инструмент '{func_name}' недоступен для этого tenant."
        ctx.tool_outputs.append({"tool": func_name, "output": tool_output})
        ctx.messages.append(ctx.provider.format_tool_result_turn(tool_call_id=tool_call_id, content=tool_output))
        return

    await ctx.emit("tool_call_start", {
        "name": func_name,
        "round": round_num,
        "args_preview": json.dumps(func_args, ensure_ascii=False)[:300],
    })
    tool_t0 = time.time()
    tool_ok = True
    if func_name in ctx.attachment_map:
        from app.services.attachments.tool import execute_attachment_search
        from app.core.config import settings as app_settings
        att_embed_provider = get_provider("ollama", app_settings.OLLAMA_BASE_URL or "http://localhost:11434")
        att_query = func_args.get("query", "")
        tool_output = await execute_attachment_search(
            attachment_id=ctx.attachment_map[func_name],
            query=att_query,
            db=ctx.db,
            provider=att_embed_provider,
            embedding_model=ctx.config.embedding_model_name or "nomic-embed-text",
        )
    else:
        # Tool config may come from the semantic-selected payload OR the full
        # tenant allow-set (model invoked something we didn't send). Use whichever
        # exists; register it into the payload so later rounds see the real schema.
        _cfg = ctx.tool_config_map.get(func_name) or ctx.all_allowed_tools_for_tenant.get(func_name)
        if _cfg is not None and func_name not in ctx.tool_config_map:
            ctx.tool_config_map[func_name] = _cfg
            if ctx.tool_defs is not None and _cfg.get("function"):
                ctx.tool_defs.append({"type": _cfg.get("type", "function"), "function": _cfg["function"]})
        result = await execute_tool(func_name, func_args, _cfg)
        tool_ok = result.success
        tool_output = result.output if result.success else f"Ошибка: {result.error}"

    _tc_latency_ms = int((time.time() - tool_t0) * 1000)
    await ctx.emit("tool_call_done", {
        "name": func_name,
        "round": round_num,
        "ok": tool_ok,
        "latency_ms": _tc_latency_ms,
        "output_chars": len(tool_output or ""),
        "output_tokens": _ct(tool_output or ""),
    })
    ctx.debug_tool_calls.append({
        "round": round_num,
        "name": func_name,
        "args_preview": json.dumps(func_args, ensure_ascii=False)[:300],
        "ok": tool_ok,
        "latency_ms": _tc_latency_ms,
        "output_chars": len(tool_output or ""),
    })
    logger.debug(f"[{ctx.correlation_id}] Tool result ({len(tool_output)} chars): {tool_output[:200]}")
    ctx.tool_outputs.append({"tool": func_name, "output": tool_output})

    # Auto tool-limit counters (per request): per-tool call count + failures.
    if ctx.tool_call_counts is not None:
        ctx.tool_call_counts[func_name] = ctx.tool_call_counts.get(func_name, 0) + 1
    if not tool_ok and ctx.failed_calls is not None:
        ctx.failed_calls[0] += 1

    if tool_ok:
        _schedule_tool_result_capture(
            ctx.capture_tasks_by_round, round_num,
            tenant_id=ctx.tenant_id, chat_id=ctx.chat_id, user_message_id=ctx.user_message_id,
            tool_name=func_name, arguments=func_args, output=tool_output or "",
            correlation_id=ctx.correlation_id,
        )

    ctx.messages.append(ctx.provider.format_tool_result_turn(tool_call_id=tool_call_id, content=tool_output))


def _schedule_tool_result_capture(
    capture_tasks_by_round: dict, round_num: int, *,
    tenant_id, chat_id, user_message_id, tool_name: str, arguments: dict, output: str, correlation_id: str,
) -> None:
    """Promote a successful tool result to a first-class artifact in the
    background (so a later turn can ground on it). Bucketed by round so the
    debug trace can list what each round captured. Best-effort: never raises."""
    try:
        from app.services.artifacts.tool_result_capture import capture_tool_result_as_artifact
        task = asyncio.create_task(capture_tool_result_as_artifact(
            tenant_id=tenant_id,
            chat_id=chat_id,
            user_message_id=uuid.UUID(str(user_message_id)) if user_message_id else None,
            tool_name=tool_name,
            arguments=arguments,
            output=output or "",
        ))
        capture_tasks_by_round.setdefault(round_num, []).append((tool_name, task))
    except Exception:
        logger.exception("[%s] tool-result capture scheduling failed (non-fatal)", correlation_id)


def _parse_tool_call(tc):
    """Parse one tool_call entry (OpenAI/Ollama dict) into
    (tool_call_id, func_name, func_args). Returns None for a non-dict entry
    (which the loop skips). func_args is decoded from a JSON string if needed."""
    if not isinstance(tc, dict):
        return None
    func_info = tc.get("function", tc)
    tool_call_id = tc.get("id", str(uuid.uuid4()))
    func_name = func_info.get("name", "")
    func_args = func_info.get("arguments", {})
    if isinstance(func_args, str):
        try:
            func_args = json.loads(func_args)
        except json.JSONDecodeError:
            func_args = {"raw": func_args}
    return tool_call_id, func_name, func_args


def _build_pinned_memory_block(memory_entries) -> str | None:
    """The '## Закреплённая память' system block. Only PINNED entries land in the
    prompt — non-pinned stay out and are reachable via the recall_memory tool, so
    the block doesn't balloon (and attention isn't self-poisoned) as memory grows."""
    pinned_only = [m for m in memory_entries if getattr(m, "is_pinned", False)]
    if not pinned_only:
        return None
    mem_lines = [f"- [{m.memory_type}] {m.content}" for m in pinned_only]
    return (
        "## Закреплённая память (always-on facts)\n"
        + "\n".join(mem_lines)
        + "\n\nДля поиска по остальной памяти — вызови tool `recall_memory(query=...)`."
    )


def _build_datetime_block(config, correlation_id: str) -> str | None:
    """The '## Текущая дата и время' system block, in the tenant's timezone.
    Non-fatal: returns None if the date can't be computed. Pure (no DB)."""
    try:
        from datetime import datetime
        now = None
        tz_label = "local"
        cfg_tz = (getattr(config, "timezone", None) or "").strip()
        if cfg_tz:
            try:
                from zoneinfo import ZoneInfo
                now = datetime.now(ZoneInfo(cfg_tz))
                tz_label = cfg_tz
            except Exception:
                logger.warning("[%s] bad tenant timezone %r — falling back to server local", correlation_id, cfg_tz)
        if now is None:
            now = datetime.now().astimezone()
            tz_label = str(now.tzinfo) if now.tzinfo else "local"
        return (
            f"## Текущая дата и время\n"
            f"Сейчас: **{now.strftime('%Y-%m-%d %H:%M')}** "
            f"({now.strftime('%A')}, {tz_label}).\n"
            f"Используй для арифметики дат («завтра», «через N дней», «в этом месяце»)."
        )
    except Exception:
        logger.exception("[pipeline] failed to compute current date (non-fatal)")
        return None


def _resolve_thinking_kwargs(
    mode: str | None,
    user_content: str,
    has_tools: bool,
    voice_mode: bool = False,
) -> dict | None:
    """Build extra_body for vLLM chat_template_kwargs.enable_thinking.
    Only models that honor this flag (Qwen3, DeepSeek-R1) react;
    others (Qwen2.5, Llama, Mistral) silently ignore it.

    `mode` ∈ {on, off, auto}.
      off  → never reason.
      on   → always reason.
      auto → reason ONLY on the FINAL (no-tools) round. On tool-routing
             rounds (has_tools=True) reasoning gets switched off because
             the model otherwise loops trying to "figure out" the tool
             schema in plain text instead of just emitting tool_calls.
             Also forced off on short queries (<100 chars) regardless of
             whether tools are present — those rarely need reasoning.

    `voice_mode` → always forces off, regardless of `mode`. The reasoning
      warmup adds ~5 s to TTFT which is unacceptable for real-time TTS."""
    # Both kwarg spellings: Qwen3/R1 honor `enable_thinking`, DeepSeek V3.1+/V4
    # honor `thinking`. Templates silently ignore the name they don't know.
    _off = {"chat_template_kwargs": {"enable_thinking": False, "thinking": False}}
    # Voice pipeline: any thinking latency is user-perceptible — force off.
    if voice_mode:
        return _off
    m = (mode or "on").lower()
    if m == "off":
        return _off
    if m == "auto":
        is_short = len((user_content or "").strip()) < 100
        # Tool-routing rounds: kill reasoning to avoid Qwen3-thinking loops
        # trying to imagine schema in prose. Short queries: don't need it.
        if has_tools or is_short:
            return _off
    # "on", or "auto" on a final no-tools long-form round → use model default.
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
from app.services.llm.system_blocks import STATIC_SYSTEM_BLOCKS
from app.services.throttle import get_or_create_throttle, ThrottleRejected
from app.services.jobs.queue import enqueue as enqueue_job
from app.models.tenant import Tenant

logger = logging.getLogger(__name__)

DEFAULT_MAX_TOOL_ROUNDS = 6  # prevent infinite tool-call loops; per-tenant override in shell_config.max_tool_rounds


def _resolve_max_tool_rounds(config) -> int:
    """Per-tenant override of the tool-loop cap, clamped to [1, 20]."""
    raw = getattr(config, "max_tool_rounds", None)
    try:
        value = int(raw) if raw is not None else DEFAULT_MAX_TOOL_ROUNDS
    except (TypeError, ValueError):
        value = DEFAULT_MAX_TOOL_ROUNDS
    return max(1, min(20, value))


def _tool_limit_auto(config) -> bool:
    """Auto tool-limit mode: replace the flat round cap with intent-aware
    guards — stop only when the model is clearly *lost* (repeated failures or
    hammering one tool), and grant a larger budget once a plan exists. A fixed
    cap can't tell legitimate multi-step work from a runaway; this can."""
    return bool(getattr(config, "tool_limit_auto", False))


def _effective_round_cap(config, plan_made: bool) -> int:
    """Round cap for the current request. In auto mode, a registered plan
    raises the cap to tool_limit_plan_rounds (diverse multi-step work is fine);
    otherwise the normal max_tool_rounds applies."""
    base = _resolve_max_tool_rounds(config)
    if _tool_limit_auto(config) and plan_made:
        try:
            plan_cap = int(getattr(config, "tool_limit_plan_rounds", 20) or 20)
        except (TypeError, ValueError):
            plan_cap = 20
        return max(base, min(40, plan_cap))
    return base


def _auto_limit_tripped(config, tool_call_counts: dict | None, failed: int) -> str | None:
    """In auto mode, return a human reason when a runaway guard trips, else
    None: (a) too many failed tool calls, (b) one tool called too many times.
    `plan`/`plan_update` are exempt from the per-tool cap (bookkeeping calls)."""
    if not _tool_limit_auto(config):
        return None
    try:
        max_fail = int(getattr(config, "tool_limit_max_failures", 4) or 4)
        max_per = int(getattr(config, "tool_limit_max_per_tool", 4) or 4)
    except (TypeError, ValueError):
        max_fail, max_per = 4, 4
    if failed >= max_fail:
        return f"достигнут лимит неудачных вызовов tool ({failed}/{max_fail})"
    for name, count in (tool_call_counts or {}).items():
        if name in ("plan", "plan_update"):
            continue
        if count > max_per:
            return f"инструмент {name} вызван слишком часто ({count} > {max_per})"
    return None
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
    voice_mode: bool = False,
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
            return await PipelineRun(
                tenant_id, chat_id, user_content, db, user_message_id, api_key_id, on_event,
                merged_message_ids, voice_mode=voice_mode,
            ).run()
    return await PipelineRun(
        tenant_id, chat_id, user_content, db, user_message_id, api_key_id, on_event,
        merged_message_ids, voice_mode=voice_mode,
    ).run()


class PipelineRun:
    """One chat-completion run. Inputs live on the instance; `run` (the
    orchestrator, assigned below) and — incrementally — the nested closures
    become methods. ToolExecCtx is the per-loop slice of this same state."""

    def __init__(self, tenant_id, chat_id, user_content, db, user_message_id=None,
                 api_key_id=None, on_event=None, merged_message_ids=None, voice_mode=False):
        self.tenant_id = tenant_id
        self.chat_id = chat_id
        self.user_content = user_content
        self.db = db
        self.user_message_id = user_message_id
        self.api_key_id = api_key_id
        self.on_event = on_event
        self.merged_message_ids = merged_message_ids
        self.voice_mode = voice_mode
        self.correlation_id = ""  # set at the start of run()

    async def _emit(self, event_type: str, payload: dict) -> None:
        """Forward a lifecycle event to the caller's on_event sink (if any),
        stamped with the run's correlation_id. Never lets a sink error escape."""
        if self.on_event is None:
            return
        try:
            await self.on_event(event_type, {"correlation_id": self.correlation_id, **payload})
        except Exception:
            logger.warning(f"[{self.correlation_id}] on_event raised; ignoring", exc_info=True)


async def _chat_completion_inner(self) -> dict:
    """
    Full LLM pipeline with tool execution support:
    1. Load shell config
    2. Load recent messages
    3. Load memory/KB/tools
    4. Build messages array
    5. Call provider
    6. If tool_calls → execute tools → feed results back → call provider again (up to max_tool_rounds)
    7. Save LLM request log
    8. Auto-summary
    9. Return response
    """
    # Inputs re-bound from the instance so the body below reads unchanged; the
    # nested closures are migrated to methods over subsequent steps.
    tenant_id = self.tenant_id
    chat_id = self.chat_id
    user_content = self.user_content
    db = self.db
    user_message_id = self.user_message_id
    api_key_id = self.api_key_id
    on_event = self.on_event
    merged_message_ids = self.merged_message_ids
    voice_mode = self.voice_mode
    self.correlation_id = correlation_id = str(uuid.uuid4())

    # Per-turn debug trace — accumulated through the pipeline, written to
    # LLMRequestLog.debug at the end. Temporary instrumentation for the
    # 100-chat offline analysis.
    debug_trace: dict = {
        "tenant_id": str(tenant_id),
        "chat_id": str(chat_id) if chat_id else None,
        "user_content_chars": len(user_content or ""),
        # Capped copy of the user query — used by the tool-modal in admin UI
        # to explain WHY a semantic-selected tool ranked where it did (the
        # cosine match was query↔description embedding).
        "user_query": (user_content or "")[:600],
        "grounding": None,
        "context": {},
        "tool_calls": [],
        "rounds": None,
        "blocks_present": [],
    }

    _emit = self._emit  # method; bound to a local so body call sites read unchanged

    await _emit("pipeline_start", {"chat_id": chat_id})

    # 1. Load config
    config = (
        await db.execute(
            select(TenantShellConfig).where(TenantShellConfig.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if not config:
        raise ValueError("Shell config not found for tenant")

    # 1a. Tier 0 routing — try the deterministic shortcut FIRST. If the query
    # is unambiguous (high-confidence single tool + required entities present
    # in text + tool has a tier0_template configured) we call the tool
    # directly and render its output via template, skipping the LLM entirely.
    # ~100-300ms vs 1-2s. If anything is uncertain → returns None and we
    # fall through to the full pipeline below.
    if getattr(config, "tier0_enabled", False):
        try:
            from app.services.llm.tier0_router import try_tier0
            tier0_result = await try_tier0(
                user_query=user_content or "",
                tenant_id=str(tenant_id),
                db=db,
                embedding_model=getattr(config, "embedding_model_name", None),
                min_tool_score=float(getattr(config, "tier0_min_tool_score", 0.80) or 0.80),
                max_score_gap=float(getattr(config, "tier0_max_score_gap", 0.15) or 0.15),
            )
        except Exception:
            logger.exception("[tier0] router crashed — falling back to LLM (non-fatal)")
            tier0_result = None
        if tier0_result is not None:
            await _emit("tier0_hit", {
                "tool": tier0_result.tool_name,
                "confidence": tier0_result.confidence,
                "latency_ms": tier0_result.latency_ms,
            })
            await _emit("done", {
                "content": tier0_result.content,
                "reasoning": None,
                "total_tokens": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "tool_calls_count": 1,
                "latency_ms": tier0_result.latency_ms,
                "model_name": "tier0",
            })
            # Lightweight log row so Tier 0 traffic is visible in stats (it
            # skips the LLM, so it would otherwise leave no trace). $0 / 0 tokens.
            db.add(LLMRequestLog(
                tenant_id=tenant_id,
                chat_id=chat_id,
                api_key_id=uuid.UUID(str(api_key_id)) if api_key_id else None,
                message_id=uuid.UUID(str(user_message_id)) if user_message_id else None,
                correlation_id=correlation_id,
                provider_type="tier0",
                model_name="tier0",
                served_by="tier0_template",
                status="success",
                latency_ms=tier0_result.latency_ms,
                time_to_first_token_ms=tier0_result.latency_ms,
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                estimated_cost=0.0,
                tool_calls_count=1,
                finish_reason="tier0",
                debug=(
                    {"tier0": {
                        "tool": tier0_result.tool_name,
                        "confidence": tier0_result.confidence,
                        "second_score": tier0_result.second_score,
                    }}
                    if getattr(config, "debug_enabled", True) else None
                ),
            ))
            # Promote the tool result to a first-class artifact — same as the
            # LLM tool loop does via _schedule_tool_result_capture. Without it
            # Tier 0 answers leave no grounding trace for follow-up turns.
            if tier0_result.tool_output:
                try:
                    from app.services.artifacts.tool_result_capture import capture_tool_result_as_artifact
                    asyncio.create_task(capture_tool_result_as_artifact(
                        tenant_id=uuid.UUID(str(tenant_id)),
                        chat_id=uuid.UUID(str(chat_id)),
                        user_message_id=uuid.UUID(str(user_message_id)) if user_message_id else None,
                        tool_name=tier0_result.tool_name,
                        arguments=tier0_result.arguments,
                        output=tier0_result.tool_output,
                    ))
                except Exception:
                    logger.exception("[tier0] tool-result capture scheduling failed (non-fatal)")
            # Auto-title: provider is not loaded on the Tier 0 fast path,
            # so pass None — the function falls back to the user query text.
            await _auto_summary_background(
                None, config, chat_id, user_content, tier0_result.content,
            )
            return {
                "content": tier0_result.content,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "latency_ms": tier0_result.latency_ms,
                "time_to_first_token_ms": tier0_result.latency_ms,
                "finish_reason": "tier0",
                "correlation_id": correlation_id,
                "provider_type": "tier0",
                "model_name": "tier0",
                "tool_calls": [],
                "tool_calls_count": 1,
                "reasoning": None,
                "response_summary": tier0_result.content[:200],
                "tool_result_summary": None,
                "attachment_summary": None,
                "context_card": None,
                "history_exclude": False,
                "context_warning": None,
                "tier0": {
                    "tool": tier0_result.tool_name,
                    "confidence": tier0_result.confidence,
                    "second_score": tier0_result.second_score,
                    "entities": tier0_result.extracted_entities,
                },
            }

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

    # PII safeguard: if the tenant has opted in AND the user query contains
    # strict-format PII (phone / MAC / IP), forbid the AutoRouter from ever
    # escalating to the heavy/cloud model for this turn. The local model
    # may be weaker, but the data stays inside our network. See model_resolver
    # AutoRouter.pick() — `pii_locked` short-circuits all escalation paths.
    if getattr(config, "pii_routing_enabled", False):
        try:
            from app.services.preprocessing.entities import extract_entities
            _pii = extract_entities(user_content or "")
            if _pii.has_any():
                router = getattr(resolved, "auto_router", None)
                if router is not None:
                    matched_kinds = [k for k, v in _pii.as_dict().items() if v]
                    router.pii_locked = True
                    router.pii_lock_reason = f"PII in user query: {', '.join(matched_kinds)}"
                    logger.info(
                        "[%s] PII routing: locked to light model (%s)",
                        correlation_id, router.pii_lock_reason,
                    )
                    debug_trace["pii_lock"] = {
                        "active": True,
                        "reason": router.pii_lock_reason,
                        "entities": _pii.as_dict(),
                    }
                    await _emit("pii_lock", {
                        "reason": router.pii_lock_reason,
                        "entities": _pii.as_dict(),
                    })
        except Exception:
            logger.exception("[%s] PII routing check failed (non-fatal)", correlation_id)
    effective_temperature = _clamp_temperature(config.temperature)
    # Pre-clamped low temperature applied ONLY when the round has tools in
    # its payload. Computed once here, picked at each call site based on
    # whether tool_defs is set for that round.
    tool_routing_temperature = _clamp_temperature(
        getattr(config, "tool_routing_temperature", 0.3) or 0.3
    )

    def _temp_for(td) -> float:
        """Choose effective temperature for one LLM call based on whether
        tools are in the payload. No-tools rounds keep the user-chosen creative
        temperature; tool rounds drop to the deterministic floor."""
        return tool_routing_temperature if td else effective_temperature

    async def _route_for_round(round_num: int) -> None:
        """If we're in auto mode, ask the router whether to swap models for
        this round. Mutates `provider`/`model_name` via the resolved holder
        AND the nonlocal locals below."""
        nonlocal provider, model_name
        router = getattr(resolved, "auto_router", None)
        if router is None:
            return
        before_name = model_name
        estimate = _estimate_round_tokens(messages, tool_defs)
        chosen, reason = await router.pick(estimate)
        if chosen.model_name != before_name:
            logger.info(
                "[%s] auto-router round %d: %s -> %s (%s, est=%d tok)",
                correlation_id, round_num, before_name, chosen.model_name, reason, estimate,
            )
            await _emit("model_switch", {
                "round": round_num,
                "from": before_name,
                "to": chosen.model_name,
                "reason": reason,
                "estimated_prompt_tokens": estimate,
            })
        provider = chosen.provider
        model_name = chosen.model_name

    logger.debug(f"[{correlation_id}] Model resolved: {model_name} (source={resolved.source}, provider={resolved.provider_type})")

    # 5. KB — semantic search via embeddings (skip if no KB documents exist)
    # kb_inject_auto=False → on-demand mode: skip pre-search entirely;
    # the LLM will call search_kb() tool when it actually needs KB context.
    kb_chunks: list = []
    _kb_inject_auto = getattr(config, "kb_inject_auto", True)
    if _kb_inject_auto and config.knowledge_base_enabled and config.embedding_model_name:
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
            semantic_floor=float(getattr(config, "tool_semantic_floor", 0.5) or 0.5),
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
        # Per-tenant description overrides are loaded from builtin_tool_overrides.
        from app.services.tools.builtin_registry import builtin_tool_config_map
        from app.services.tools.builtin_overrides import load_overrides_for_tenant
        _builtin_overrides = await load_overrides_for_tenant(db, tenant_id)
        for _bt_name, _bt_cfg in builtin_tool_config_map(_builtin_overrides).items():
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
                "timezone": (getattr(config, "timezone", None) or None),
                "user_message_id": str(user_message_id) if user_message_id else None,
            }

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
        for _bt_name, _bt_cfg in builtin_tool_config_map(_builtin_overrides).items():
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
            "timezone": (getattr(config, "timezone", None) or None),
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
    # Per-block labels parallel to system_parts — used by prompt_layout to
    # show admins which piece came from which source ("BLOCK-MEMORY-A",
    # "ontology_prompt", "HARDCODED-2"), instead of one opaque megablob.
    system_block_labels: list[str] = []

    def _sys(label: str, content: str) -> None:
        if not content:
            return
        system_parts.append(content)
        system_block_labels.append(label)

    # Language pin — first system part so the lock is the very first thing the
    # model sees. Tenant chooses the language in shell config (default 'ru').
    from app.services.llm.language import build_language_pin_text
    _sys("Language pin", build_language_pin_text(getattr(config, "response_language", "ru")))

    # === [HARDCODED-0] current date/time — computed here, injected LAST ===
    # KV-cache optimisation: date/time changes every minute — putting it first
    # invalidates the cache for ALL subsequent static blocks on every request.
    # So we compute it here but defer _sys() to just before the history section,
    # keeping the long static prefix (rules, tools, KB, memory) cacheable.
    _hc0_date_text = _build_datetime_block(config, correlation_id)

    if config.system_prompt:
        _sys("Tenant system_prompt", config.system_prompt)
    if getattr(config, "ontology_prompt", None) and config.ontology_prompt.strip():
        _sys("Tenant ontology_prompt", config.ontology_prompt.strip())
    if config.rules_text:
        _sys("Tenant rules_text", f"Rules:\n{config.rules_text}")

    if False:  # === [HARDCODED-1] language hint ===
        _sys(
            "HARDCODED-1 language hint",
            "Отвечай на том же языке, на котором обращается пользователь "
            "(русский → русский, украинский → украинский, английский → английский). "
            "Технические термины (IP, MAC, DHCP, VLAN, BGP) оставляй как есть."
        )

    # === [HARDCODED-2,3,4,8,7] static, tenant-agnostic instruction blocks ===
    # Moved to app/services/llm/system_blocks.py (separates prompt content from
    # orchestration). Order preserved; always appended (formerly `if True`).
    for _label, _text in STATIC_SYSTEM_BLOCKS:
        _sys(_label, _text)

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
            _sys(
                "BLOCK-MEMORY-A api key + group",
                "## Память API-ключа\n"
                + "\n".join(f"- {item}" for item in api_key_memory_items),
            )

    _memory_block_text: str | None = None
    _kb_block_text: str | None = None
    _attachments_block_text: str | None = None
    # === [BLOCK-MEMORY-B] PINNED memory entries from DB ===
    _memory_block_text = _build_pinned_memory_block(memory_entries)
    if _memory_block_text:
        _sys("BLOCK-MEMORY-B pinned facts", _memory_block_text)

    if _kb_inject_auto:  # === [BLOCK-KB] knowledge base excerpts ===
        # Semantic top-K KB chunks for the current user message. These are
        # background domain knowledge — not the user's artifacts. Stays in
        # `system` (not user-message) because it's reference material, not
        # something we expect the model to edit or treat as the subject.
        # Empty/low-quality result → nothing emitted; model can fall back to
        # the `search_kb` tool for a wider query.
        # kb_inject_auto=False → skip entirely; model calls search_kb on demand.
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
            _sys("BLOCK-KB excerpts", _kb_block_text)

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
            _sys("BLOCK-ATTACHMENTS prior files", _attachments_block_text)

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
        debug_trace["grounding"] = {
            "count": len(active_artifacts or []),
            "picks": [
                {
                    "id": str(a.id),
                    "kind": a.kind,
                    "label": (getattr(a, "label", None) or "")[:80],
                    "similarity": float(getattr(a, "_grounding_score", 0.0) or 0.0) or None,
                    "source": getattr(a, "_grounding_source", None),
                }
                for a in (active_artifacts or [])
            ],
        }
    except Exception:
        logger.exception("[pipeline] artifact auto-grounding failed (non-fatal)")
        debug_trace["grounding"] = {"error": "grounding_failed"}

    # === [BLOCK-HISTORY-RESUMES] — agentic memory ===
    # Three-layer history, budget-driven (config.history_budget_tokens):
    #   1. Last RAW_LAST_PAIRS pairs — VERBATIM, appended as native chat roles
    #      (see `history_role_msgs` consumed below). Chat templates are trained
    #      on dialogue turns, weak models track the thread much better this
    #      way — and follow-ups («а какой у него IP?») need the exact text.
    #   2. Older pairs — one-line resumes (concrete values stripped by design),
    #      NEWEST first, while the token budget lasts.
    #   3. Beyond the budget — the rolling chat summary, when present.
    # Full original content stays reachable via recall_chat / get_message.
    # N pairs considered = config.max_context_messages (treated as pair-count).
    chat = (await db.execute(select(Chat).where(Chat.id == chat_id))).scalar_one_or_none()
    history_role_msgs: list[dict] = []
    try:
        n_pairs = max(0, int(getattr(config, "max_context_messages", 0) or 0))
        budget_tokens = max(500, int(getattr(config, "history_budget_tokens", 3000) or 3000))
        budget_left = budget_tokens
        dropped_pairs = 0
        resume_pair_count = 0
        raw_pair_count = 0

        def _est_tokens(s: str) -> int:
            # ~3 chars/token for Cyrillic on Qwen-family tokenizers.
            return len(s) // 3 + 1

        if n_pairs > 0:
            # Pull the last N user messages REGARDLESS of whether their resume
            # has been generated yet. The resume_query filter we used to have
            # here caused turn-2-of-a-fresh-chat to lose all history (the
            # background resume task hadn't finished). For pairs without a
            # resume we fall back to the trimmed full content — concrete values
            # may appear here but it's still THIS chat's recent turns, which
            # is the most trustworthy local source we have.
            recent_user_q = (
                select(Message)
                .where(
                    Message.tenant_id == tenant_id,
                    Message.chat_id == chat_id,
                    Message.role == "user",
                )
                .order_by(Message.created_at.desc())
                .limit(n_pairs + 1)  # +1 to potentially drop current user msg if it slipped in
            )
            user_rows = list(reversed((await db.execute(recent_user_q)).scalars().all()))
            # Exclude the current user message if present (its content is
            # appended explicitly to the prompt below).
            if user_message_id:
                cur_id_s = str(user_message_id)
                user_rows = [u for u in user_rows if str(u.id) != cur_id_s]
            user_rows = user_rows[-n_pairs:]

            # Char caps. The verbatim layer is generous — it carries the data
            # the user is most likely to follow up on. The FULL-content
            # fallback for un-resumed pairs in the resume layer stays tight
            # (token bloat bound).
            RAW_LAST_PAIRS = 2
            RAW_USER_CAP = 1500
            RAW_ASSISTANT_CAP = 2000
            FULL_USER_CAP = 400
            FULL_ASSISTANT_CAP = 700

            def _trim(text: str | None, cap: int) -> str:
                t = (text or "").strip()
                if not t:
                    return ""
                t = t.replace("\r", "")
                if len(t) > cap:
                    return t[:cap].rstrip() + " …"
                return t

            pairs: list[tuple] = []
            for u in user_rows:
                asst = (await db.execute(
                    select(Message).where(
                        Message.chat_id == chat_id,
                        Message.role == "assistant",
                        Message.created_at >= u.created_at,
                    ).order_by(Message.created_at.asc()).limit(1)
                )).scalar_one_or_none()
                pairs.append((u, asst))

            # Layer 1 — verbatim native-role turns. Critical for short
            # follow-ups like «да», «ок», «а во второй строке?» — the resume
            # strips exactly what's being referenced.
            raw_pairs = pairs[-RAW_LAST_PAIRS:] if RAW_LAST_PAIRS > 0 else []
            older_pairs = pairs[: len(pairs) - len(raw_pairs)]
            for u, asst in raw_pairs:
                q_full = _trim(u.content, RAW_USER_CAP)
                a_full = _trim(asst.content if asst else None, RAW_ASSISTANT_CAP)
                if not q_full:
                    continue
                history_role_msgs.append({"role": "user", "content": q_full})
                if a_full:
                    history_role_msgs.append({"role": "assistant", "content": a_full})
                budget_left -= _est_tokens(q_full) + _est_tokens(a_full)
                raw_pair_count += 1

            # Layer 2 — resume lines, NEWEST first; stop once an entry no
            # longer fits (contiguous window — holes in the middle of history
            # confuse the model more than a clean cut).
            resume_lines_rev: list[str] = []
            has_full_content = False
            older_rev = list(reversed(older_pairs))
            for idx, (u, asst) in enumerate(older_rev):
                # Anchor the id on the assistant message when present — that's the
                # row that owns artifacts, and the row the model fetches via get_message.
                anchor_id = str(asst.id) if asst else str(u.id)
                u_resume = (u.resume_query or "").strip()
                a_resume = (asst.resume_response if asst else None) or ""
                a_resume = a_resume.strip()

                # Anchor id is the assistant message id — explicit msg: prefix
                # so the model never confuses it with an artifact_id (which
                # appears later as `(artifact_id=...)`). Maps cleanly to
                # get_message(id) in the footer.
                anchor_token = f"msg:{anchor_id}"
                if u_resume and a_resume:
                    # Sanitized form — older turn with both resumes generated.
                    entry = f"- [{anchor_token}] Q: {u_resume} → A: {a_resume}"
                else:
                    # Raw form — resume isn't ready yet. Marked so the model
                    # knows concrete values are quotable here.
                    has_full_content = True
                    q_full = _trim(u.content, FULL_USER_CAP)
                    a_full = _trim(asst.content if asst else None, FULL_ASSISTANT_CAP)
                    entry = (
                        f"- [{anchor_token}] (raw, без резюме)\n"
                        f"  Q: {q_full or '(пусто)'}\n"
                        f"  A: {a_full or '(нет ответа)'}"
                    )
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
                    entry += (
                        f"\n  📎 [{kind}] {label} (artifact_id={aid})"
                        if aid else f"\n  📎 [{kind}] {label}"
                    )
                cost = _est_tokens(entry)
                if cost > budget_left:
                    dropped_pairs = len(older_rev) - idx
                    break
                budget_left -= cost
                resume_lines_rev.append(entry)
            resume_pair_count = len(resume_lines_rev)
            resume_lines = list(reversed(resume_lines_rev))
            if resume_lines:
                # ID mapping (важно — у модели часто путаются эти два tool'а):
                #   [msg:<uuid>]            → get_message(id="<uuid>")
                #   (artifact_id=<uuid>)    → get_artifact(id="<uuid>")
                id_mapping = (
                    "\n\nID и tool'ы:\n"
                    "- `[msg:<uuid>]` в шапке обмена → `get_message(id=\"<uuid>\")` "
                    "(полный текст вопроса+ответа, плюс ссылки на артефакты).\n"
                    "- `(artifact_id=<uuid>)` после маркера 📎 → "
                    "`get_artifact(id=\"<uuid>\")` (досл. содержимое артефакта).\n"
                    "- `find_artifacts(kind=..., query=...)` — если артефакт не упомянут.\n"
                    "Не путай: msg-id ≠ artifact-id. Если зовёшь не тот tool — получишь not-found."
                )
                footer = (
                    "\n\nПомечены `(raw, без резюме)` — свежие обмены, резюме ещё "
                    "не сгенерилось; их полный текст — надёжный источник конкретики.\n"
                    "Остальные строки — резюме без конкретных значений (IP, числа, имена) "
                    "специально, чтобы исключить искажения. За конкретикой по ним:"
                    + id_mapping
                ) if has_full_content else (
                    "\n\nРезюме не содержат конкретных значений (IP, числа, имена) — это специально, "
                    "чтобы исключить искажения. Конкретику бери из:\n"
                    "- блока «Активные артефакты» (если есть)."
                    + id_mapping
                )
                _sys(
                    "HISTORY-RESUMES recent exchanges",
                    "## Более ранние обмены этого чата (сжато; самые свежие идут "
                    "ниже обычными репликами диалога)\n"
                    + "\n".join(resume_lines)
                    + footer,
                )

            # Layer 3 — rolling chat summary covers what didn't fit (budget
            # cut or beyond the n_pairs window). Generated in background /
            # via admin endpoint; absent for most fresh chats — that's fine.
            summary_text = ((chat.history_summary if chat else None) or "").strip()
            if summary_text and (dropped_pairs > 0 or total_messages_count > 2 * (n_pairs + 1)):
                _sys(
                    "BLOCK-HISTORY-SUMMARY older context",
                    "## Сводка более раннего диалога (старше обменов выше)\n" + summary_text,
                )

        debug_trace["history"] = {
            "budget_tokens": budget_tokens,
            "budget_left_tokens": max(0, budget_left),
            "raw_pairs": raw_pair_count,
            "resume_pairs": resume_pair_count,
            "dropped_pairs": dropped_pairs,
        }
    except Exception:
        logger.exception("[pipeline] failed to assemble HISTORY-RESUMES block")

    # === [HARDCODED-0] date/time injection point ===
    # Injected HERE (after all static blocks) for KV-cache efficiency:
    # everything above is tenant-static and can be cached across requests.
    # Only the date + history + query below are dynamic.
    if _hc0_date_text:
        _sys("HARDCODED-0 current date/time", _hc0_date_text)

    messages: list[dict] = []
    if system_parts:
        messages.append({"role": "system", "content": "\n\n".join(system_parts)})

    # Layer-1 verbatim history (built in BLOCK-HISTORY-RESUMES above) goes
    # right after the system message in native dialogue roles.
    if history_role_msgs:
        messages.extend(history_role_msgs)

    # `chat` is loaded above (BLOCK-HISTORY-RESUMES).
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

    # Final language reminder — strongest position (last message before the
    # model generates). The "Language pin" system block sits high in the prompt;
    # when the user's question and inlined artifact data are in another language
    # (e.g. Ukrainian client data), that nearby signal can out-pull the distant
    # pin and the model drifts. A terminal reminder from the tenant's CONFIGURED
    # response_language (NOT auto-detected from input — that would defeat a fixed
    # output language) re-anchors it. Skipped when no language is configured.
    _resp_lang = getattr(config, "response_language", None)
    if _resp_lang:
        from app.services.llm.language import build_language_pin_text
        messages.append({
            "role": "system",
            "content": "📌 ЯЗЫК ОТВЕТА (приоритет над языком запроса и данных): "
                       + build_language_pin_text(_resp_lang),
        })

    # Merge tenant tools + attachment search tools only when the request and model support tools.
    all_tool_defs = [_public_tool_def(t.config_json) for t in tools if t.config_json] if tools else []
    all_tool_defs = all_tool_defs + attachment_tool_defs
    # Builtin tools — system toolset (memory/artifacts/RAG). Always exposed
    # to the model regardless of semantic budget; lives in code, not DB.
    if tools_enabled:
        from app.services.tools.builtin_registry import builtin_tools_for_payload, BUILTIN_TOOL_NAMES
        # _builtin_overrides is populated above when tools_enabled is true.
        all_tool_defs = builtin_tools_for_payload(_builtin_overrides) + all_tool_defs

    # Capture per-tool selection metadata for debug-trace BEFORE trimming so
    # we can show in UI "why this tool is here" (pinned/builtin/semantic+score).
    try:
        debug_payload: list[dict] = []
        tools_by_name = {
            ((t.config_json or {}).get("function") or {}).get("name"): t
            for t in (tools or [])
            if t.config_json
        }
        for td in (all_tool_defs or []):
            fn = (td or {}).get("function") or {}
            name = fn.get("name")
            if not name:
                continue
            desc = fn.get("description") or ""
            params = fn.get("parameters") or {}
            source = "unknown"
            similarity = None
            t_obj = tools_by_name.get(name)
            if tools_enabled and name in BUILTIN_TOOL_NAMES:
                source = "builtin"
            elif name and name.startswith("search_attachment_"):
                source = "attachment"
            elif t_obj is not None:
                source = getattr(t_obj, "_selection_source", "selected")
                _ss = getattr(t_obj, "_semantic_score", None)
                if isinstance(_ss, (int, float)):
                    similarity = round(float(_ss), 3)
            debug_payload.append({
                "name": name,
                "source": source,
                "similarity": similarity,
                "description_chars": len(desc),
                "parameters_chars": len(json.dumps(params, ensure_ascii=False)) if params else 0,
                "description": desc[:1200],
                "parameters": params,
            })
        debug_trace["tools_payload"] = debug_payload
    except Exception:
        logger.exception("[pipeline] failed to build tools_payload debug snapshot")

    # Snapshot the FULL catalog of every tool the tenant could call this
    # request — used by builtin `describe_tool(name)`. Built from the genuine
    # full allow-set (`all_allowed_tools_for_tenant`: every active tenant tool
    # + builtins), NOT from `all_tool_defs` — the latter is only the
    # semantically-selected payload subset, so a tool excluded by the semantic
    # floor would be callable-by-name yet invisible to describe_tool. That's
    # exactly the case describe_tool exists to serve: fetch the schema of a
    # tool the model knows by name (from ontology/history/KB) but that wasn't
    # ranked into the payload. Union with all_tool_defs to also cover
    # attachment search tools, which live only there.
    if tools_enabled and (all_allowed_tools_for_tenant or all_tool_defs):
        _full_catalog_by_name: dict[str, dict] = {}
        for _cfg in all_allowed_tools_for_tenant.values():
            td = _public_tool_def(_cfg)
            nm = (td.get("function") or {}).get("name")
            if nm:
                _full_catalog_by_name[nm] = td
        for td in all_tool_defs:
            nm = (td.get("function") or {}).get("name")
            if nm and nm not in _full_catalog_by_name:
                _full_catalog_by_name[nm] = td
        for _name, _cfg in tool_config_map.items():
            ctx = _cfg.get("_context")
            if isinstance(ctx, dict):
                ctx["full_tool_catalog"] = _full_catalog_by_name
        for _name, _cfg in all_allowed_tools_for_tenant.items():
            ctx = _cfg.get("_context")
            if isinstance(ctx, dict):
                ctx["full_tool_catalog"] = _full_catalog_by_name

    # Lazy tool catalog: keep the top-K tools by semantic score in `tools=[...]`
    # with their full schema, and demote the rest to a compact system-block
    # listing (name + 1-line + tags). The compact tools are still callable —
    # pipeline auto-adds their schema to the payload on the round AFTER the
    # model first invokes them, and the `describe_tool(name)` builtin lets
    # the model inspect them up-front. Builtin / pinned / attachment-search
    # tools NEVER get demoted (they're system-essential and small enough).
    lazy_topk = int(getattr(config, "lazy_tool_catalog_topk", 0) or 0)
    if all_tool_defs and tools_enabled and lazy_topk > 0:
        from app.services.tools.builtin_registry import BUILTIN_TOOL_NAMES as _BTN
        # Score each TenantTool we picked (semantic/keyword/pinned). Builtin
        # and attachment-* are protected (never compact). Pinned: from
        # `_selection_source = pinned` we know admin marked them important.
        protected_names: set[str] = set()
        scored_names: list[tuple[float, str]] = []
        for t in (tools or []):
            cfg_fn = (t.config_json or {}).get("function") or {}
            n = cfg_fn.get("name")
            if not n:
                continue
            src = getattr(t, "_selection_source", "") or ""
            if src == "pinned":
                protected_names.add(n)
                continue
            sc = getattr(t, "_semantic_score", None)
            scored_names.append((float(sc) if isinstance(sc, (int, float)) else 0.0, n))
        scored_names.sort(reverse=True)
        full_names: set[str] = {n for _, n in scored_names[:lazy_topk]} | protected_names

        full_defs: list[dict] = []
        compact_defs: list[dict] = []
        for td in all_tool_defs:
            n = (td.get("function") or {}).get("name")
            if not n:
                continue
            # Builtins and attachment search always full — small, system-critical.
            if n in _BTN or n.startswith("search_attachment_"):
                full_defs.append(td)
                continue
            if n in full_names:
                full_defs.append(td)
            else:
                compact_defs.append(td)

        if compact_defs:
            # Render compact catalog as an additional system message AFTER
            # messages[0]. The main system block is already built by this
            # point, so we can't extend system_parts — but a second `role:
            # system` message gets attended to the same way.
            lines = ["## Доп. tools (compact — полная schema по `describe_tool(name)` или прямому вызову по имени)"]
            for td in compact_defs:
                fn = td.get("function") or {}
                nm = fn.get("name") or ""
                d = (fn.get("description") or "").splitlines()[0].strip()
                if len(d) > 140:
                    d = d[:140].rstrip() + "…"
                lines.append(f"- `{nm}` — {d}")
            # Insert AFTER the main system block but BEFORE the user message.
            # messages[0] is system. messages[-1] is the user. Insert at index 1.
            compact_block = {"role": "system", "content": "\n".join(lines)}
            insert_at = 1 if messages and messages[0].get("role") == "system" else 0
            messages.insert(insert_at, compact_block)
            logger.info(
                "[%s] lazy-catalog: %d full + %d compact (topk=%d)",
                correlation_id, len(full_defs), len(compact_defs), lazy_topk,
            )
        # Catalog is already injected above (before the split) so describe_tool
        # works regardless of whether anything got demoted to compact.
        # Replace all_tool_defs with the trimmed-by-lazy version for trim_tool_definitions below.
        all_tool_defs = full_defs

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
    # Per-round token breakdown for multi-tool requests. Each entry is one
    # LLM round-trip (round=0 = initial call, 1..N = follow-ups after each
    # tool exec). Exposed via provider_call_done SSE + saved into assistant
    # message metadata for later inspection in admin Logs tab.
    round_breakdown: list[dict] = []
    # Tasks created by capture_tool_result_as_artifact in this request,
    # keyed by round → list[(tool_name, awaitable Task)]. Awaited before
    # writing the LLMRequestLog so debug.rounds[].artifacts_captured can
    # carry the freshly-minted artifact_id back to the UI.
    _capture_tasks_by_round: dict[int, list[tuple[str, "asyncio.Task"]]] = {}

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
        # Per-call + provider-round state/deps — built once, mutated in place,
        # read back after the loop.
        _tool_ctx = ToolExecCtx(
            messages=messages, tool_outputs=tool_outputs_current_request,
            tool_config_map=tool_config_map, tool_defs=tool_defs,
            capture_tasks_by_round=_capture_tasks_by_round, debug_tool_calls=debug_trace["tool_calls"],
            allowed_tool_names=allowed_tool_names, attachment_map=attachment_map,
            all_allowed_tools_for_tenant=all_allowed_tools_for_tenant,
            provider=provider, db=db, config=config,
            tenant_id=tenant_id, chat_id=chat_id, user_message_id=user_message_id,
            correlation_id=correlation_id, emit=_emit,
            model_name=model_name, user_content=user_content, chunk_cb=chunk_cb,
            current_round_ref=current_round_ref, round_breakdown=round_breakdown,
            tool_routing_temperature=tool_routing_temperature, effective_temperature=effective_temperature,
            tool_call_counts={}, failed_calls=[0],
        )

        # Initial LLM call — let the auto-router pick light/heavy first
        await _route_for_round(0)
        resp = await _run_provider_round(_tool_ctx, 0, voice_mode=voice_mode)
        total_prompt_tokens += int(resp.prompt_tokens or 0)
        total_completion_tokens += int(resp.completion_tokens or 0)

        # Tool execution loop
        round_num = 0
        max_tool_rounds = _resolve_max_tool_rounds(config)
        _auto_stop_reason: str | None = None
        while resp.tool_calls:
            # Effective cap: in auto mode a registered plan unlocks a bigger
            # budget; otherwise the flat max_tool_rounds applies.
            _plan_made = (_tool_ctx.tool_call_counts or {}).get("plan", 0) > 0
            _cap = _effective_round_cap(config, _plan_made)
            if round_num >= _cap:
                break
            # Auto guards: stop early if the model is lost (repeated failures or
            # hammering one tool). Checked BEFORE the next round so the offending
            # call isn't issued again.
            _auto_stop_reason = _auto_limit_tripped(
                config, _tool_ctx.tool_call_counts, (_tool_ctx.failed_calls or [0])[0]
            )
            if _auto_stop_reason:
                logger.info("[%s] auto tool-limit stop: %s", correlation_id, _auto_stop_reason)
                await _emit("tool_limit_stop", {"reason": _auto_stop_reason})
                break
            round_num += 1
            tool_calls_total += len(resp.tool_calls)

            logger.debug(f"[{correlation_id}] Tool round {round_num}: {len(resp.tool_calls)} call(s)")

            # Add assistant message with tool_calls to conversation —
            # provider decides what extra fields (reasoning_content, etc.) to echo.
            messages.append(provider.format_assistant_turn(resp))

            # Execute each tool call and add results (see _execute_tool_call).
            for tc in resp.tool_calls:
                await _execute_tool_call(_tool_ctx, tc, round_num)

            # Summarize large tool results from PREVIOUS rounds to save tokens.
            # Current round results stay full so LLM can process them now.
            # After LLM sees them, they'll be summarized in the next iteration.
            summary_prompt_tokens, summary_completion_tokens = await _summarize_old_tool_results(
                messages, round_num, provider, model_name, correlation_id
            )
            total_prompt_tokens += summary_prompt_tokens
            total_completion_tokens += summary_completion_tokens

            # Call LLM again with the tool results (see _run_provider_round).
            resp = await _run_provider_round(_tool_ctx, round_num)
            total_prompt_tokens += int(resp.prompt_tokens or 0)
            total_completion_tokens += int(resp.completion_tokens or 0)

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
            and round_num < _resolve_max_tool_rounds(config)
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
                temperature=_temp_for(tool_defs),
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
            while resp.tool_calls and round_num < _resolve_max_tool_rounds(config):
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
                        _schedule_tool_result_capture(
                            _capture_tasks_by_round, round_num,
                            tenant_id=tenant_id, chat_id=chat_id, user_message_id=user_message_id,
                            tool_name=func_name, arguments=func_args, output=tool_output or "",
                            correlation_id=correlation_id,
                        )
                    messages.append(provider.format_tool_result_turn(
                        tool_call_id=tool_call_id,
                        content=tool_output,
                    ))
                summary_prompt_tokens, summary_completion_tokens = await _summarize_old_tool_results(
                    messages, round_num, provider, model_name, correlation_id
                )
                total_prompt_tokens += summary_prompt_tokens
                total_completion_tokens += summary_completion_tokens
                # Re-route per round: now that more tool-results are in,
                # context may have crossed the size threshold.
                await _route_for_round(round_num)
                await _emit("provider_call_start", {"round": round_num, "model": model_name})
                provider_t0 = time.time()
                current_round_ref["round"] = round_num
                resp = await provider.chat_completion(
                    messages=messages,
                    model=model_name,
                    temperature=_temp_for(tool_defs),
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

        # If we exhausted the tool-rounds cap while the model still wanted
        # more tools (and produced no useful content), force one more LLM call
        # WITHOUT tools — instructing it to summarize what it has so the user
        # gets at least a partial answer instead of a blank assistant message.
        _cap = _effective_round_cap(config, (_tool_ctx.tool_call_counts or {}).get("plan", 0) > 0)
        if (
            resp
            and resp.tool_calls
            and (round_num >= _cap or _auto_stop_reason)
            and not (resp.content or "").strip()
        ):
            logger.info(
                f"[{correlation_id}] Tool loop ended ({_auto_stop_reason or f'cap {_cap}'}); "
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
            # plan_update exception: if a plan was registered this request and
            # progress wasn't marked yet, the wrap-up call may use exactly one
            # tool — plan_update — so the checklist doesn't stay empty when
            # the round cap cuts execution short. Detected by the tool-result
            # prefixes we control (executor's plan/plan_update outputs).
            _final_tools = None
            if tools_enabled:
                _plan_ran = any(
                    isinstance(m, dict) and m.get("role") == "tool"
                    and str(m.get("content") or "").startswith("План записан")
                    for m in messages
                )
                _plan_marked = any(
                    isinstance(m, dict) and m.get("role") == "tool"
                    and str(m.get("content") or "").startswith("Прогресс отмечен")
                    for m in messages
                )
                if _plan_ran and not _plan_marked:
                    from app.services.tools.builtin_registry import BUILTIN_TOOLS
                    _pu = next(
                        (t for t in BUILTIN_TOOLS if t["function"]["name"] == "plan_update"),
                        None,
                    )
                    if _pu:
                        _final_tools = [{"type": "function", "function": _pu["function"]}]
            messages.append({
                "role": "system",
                "content": (
                    "Достигнут лимит вызовов инструментов в этом раунде. "
                    "Сформулируй ответ пользователю на основе уже полученных данных. "
                    "Если данных недостаточно — честно скажи об этом и предложи следующие шаги. "
                    + (
                        "Разрешён ровно один tool — plan_update(done=[...], failed=[...]): "
                        "отметь им фактически выполненные шаги плана. Другие tools НЕ вызывай."
                        if _final_tools else
                        "НЕ вызывай tools в этом ответе."
                    )
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
                    tools=_final_tools,  # None unless the plan_update exception applies
                    on_chunk=chunk_cb,
                    extra_body={"chat_template_kwargs": {"enable_thinking": False, "thinking": False}},  # final summary fast
                )
                if resp.prompt_tokens:
                    total_prompt_tokens += resp.prompt_tokens
                if resp.completion_tokens:
                    total_completion_tokens += resp.completion_tokens
                if _final_tools and resp.tool_calls:
                    # Execute plan_update (and only it), then one last text-only call.
                    round_num += 1
                    tool_calls_total += len(resp.tool_calls)
                    messages.append(provider.format_assistant_turn(resp))
                    for tc in resp.tool_calls:
                        parsed = _parse_tool_call(tc)
                        if parsed and parsed[1] == "plan_update":
                            await _execute_tool_call(_tool_ctx, tc, round_num)
                        elif parsed:
                            messages.append(provider.format_tool_result_turn(
                                tool_call_id=parsed[0],
                                content="(Лимит исчерпан — разрешён только plan_update.)",
                            ))
                    current_round_ref["round"] = round_num + 1
                    resp = await provider.chat_completion(
                        messages=messages,
                        model=model_name,
                        temperature=effective_temperature,
                        max_tokens=config.max_tokens,
                        tools=None,
                        on_chunk=chunk_cb,
                        extra_body={"chat_template_kwargs": {"enable_thinking": False, "thinking": False}},
                    )
                    if resp.prompt_tokens:
                        total_prompt_tokens += resp.prompt_tokens
                    if resp.completion_tokens:
                        total_completion_tokens += resp.completion_tokens
                await _emit("provider_call_done", {
                    "round": current_round_ref["round"],
                    "latency_ms": int((time.time() - provider_t0) * 1000),
                    "has_tool_calls": False,
                    "content_chars": len(resp.content or ""),
                    "reasoning_chars": len(resp.reasoning or ""),
                    "final_summary": True,
                })
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

    # Per-section token/char telemetry for the assembled prompt.
    context_breakdown = _compute_context_breakdown(
        messages, config, memory_entries, kb_chunks, tool_defs, correlation_id
    )

    norm_req = {
        "messages_count": len(messages),
        "model": model_name,
        "tools_count": len(tool_defs) if tool_defs else 0,
        "tool_rounds": tool_calls_total,
        "context_breakdown": context_breakdown,
        "prompt_layout": _build_prompt_layout(
            messages,
            tool_defs,
            tool_mode=needs_tools,
            system_block_labels=system_block_labels,
            system_block_contents=system_parts,
        ),
    }
    norm_resp = _build_normalized_response(resp, messages, tool_calls_total)

    message_uuid = None
    if user_message_id:
        try:
            message_uuid = uuid.UUID(str(user_message_id))
        except (ValueError, TypeError):
            message_uuid = None

    # Attribute background-captured artifact ids back to their round (see helper).
    await _collect_capture_artifacts(_capture_tasks_by_round, round_breakdown, correlation_id)

    # Finalize debug trace before persisting.
    debug_trace["rounds"] = round_breakdown
    debug_trace["context"] = {
        "messages_count": len(messages),
        "memory_count": len(memory_entries),
        "kb_chunks_count": len(kb_chunks),
        "tools_count": len(tools),
        "tool_names": [t.get("function", {}).get("name") for t in (tool_defs or []) if t.get("function")],
    }
    debug_trace["blocks_present"] = [
        name for name, present in (
            ("ACTIVE-ARTIFACTS", bool(active_artifacts_block_text)),
            ("MEMORY", bool(memory_entries)),
            ("KB", bool(kb_chunks)),
            ("ATTACHMENTS", bool(locals().get("_attachments_block_text"))),
        ) if present
    ]
    debug_trace["config_snapshot"] = {
        "model_name": model_name,
        "provider_type": resolved.provider_type,
        "embedding_model": getattr(config, "embedding_model_name", None),
        "response_language": getattr(config, "response_language", None),
        "max_context_messages": getattr(config, "max_context_messages", None),
    }
    debug_trace["final"] = {
        "status": status,
        "finish_reason": resp.finish_reason if resp else None,
        "content_chars": len(resp.content or "") if resp else 0,
        "reasoning_chars": len(getattr(resp, "reasoning", "") or "") if resp else 0,
        "latency_ms": latency,
    }

    log = LLMRequestLog(
        tenant_id=tenant_id,
        chat_id=chat_id,
        api_key_id=uuid.UUID(str(api_key_id)) if api_key_id else None,
        message_id=message_uuid,
        correlation_id=correlation_id,
        provider_type=resolved.provider_type,
        model_name=model_name,
        served_by="llm",
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
        # Per-tenant switch: when off, we skip persisting the (potentially
        # large) debug snapshot. The trace was still assembled in memory
        # because pipeline branches rely on it for streaming events.
        debug=(debug_trace if getattr(config, "debug_enabled", True) else None),
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

        # Background enrichment is now durable (survives restarts, retries):
        # enqueue rows commit with this request's session. See app/services/jobs.
        if config.memory_enabled and resolved.provider_type != "ollama" and MEMORY_AUTO_EXTRACT:
            await enqueue_job(db, "memory_extract", {
                "tenant_id": str(tenant_id),
                "chat_id": str(chat_id),
                "user_content": user_content,
                "assistant_content": resp.content,
            }, tenant_id=tenant_id)

        history_for_summary = history_dicts + [
            {"role": "user", "content": user_content},
            {"role": "assistant", "content": resp.content},
        ]
        if len(history_for_summary) > RECENT_MESSAGES_FULL:
            summary_target_count = max(total_messages_count + 1 - RECENT_MESSAGES_FULL, 0)
            # Bound the payload: the summarizer only uses role+content of the
            # last ~30 messages, so trim before persisting the job.
            _old = history_for_summary[:-RECENT_MESSAGES_FULL]
            _old_min = [{"role": m.get("role"), "content": (m.get("content") or "")[:600]} for m in _old][-40:]
            await enqueue_job(db, "history_summary", {
                "tenant_id": str(tenant_id),
                "chat_id": str(chat_id),
                "old_messages": _old_min,
                "existing_summary": chat.history_summary if chat else None,
                "message_count_up_to": summary_target_count,
            }, tenant_id=tenant_id)

        if chat and (not chat.title or not chat.description):
            await _auto_summary_background(
                provider,
                config,
                chat_id,
                user_content,
                resp.content,
                fallback_model_name=summary_model,
            )

    # 11. Compute context-pressure warning.
    # Two signals:
    #   (a) peak prompt_tokens of any round vs model.max_context_tokens — if
    #       ≥85% we're filling the model's window and risk truncation/error.
    #   (b) we're capped by max_context_messages and there's much more history
    #       than we sent — model can't see older context, may give worse answers.
    context_warning: dict | None = None
    try:
        max_ctx = resolved.max_context_tokens or 0
        peak_prompt = max((r.get("prompt_tokens", 0) for r in round_breakdown), default=0)
        if max_ctx and peak_prompt:
            ratio = peak_prompt / max_ctx
            if ratio >= 0.85:
                context_warning = {
                    "kind": "near_model_limit",
                    "ratio": round(ratio, 3),
                    "prompt_tokens": peak_prompt,
                    "max_context_tokens": max_ctx,
                    "message": (
                        f"Промпт занял {peak_prompt:,} токенов из {max_ctx:,} "
                        f"({int(ratio*100)}%) — близко к лимиту окна модели. "
                        "Сократи историю или переключись на модель с большим контекстом."
                    ).replace(",", " "),
                }
        if context_warning is None:
            cap = int(getattr(config, "max_context_messages", 0) or 0)
            if cap and total_messages_count > cap * 2:
                context_warning = {
                    "kind": "history_truncated",
                    "cap": cap,
                    "total": total_messages_count,
                    "message": (
                        f"Из {total_messages_count} сообщений чата в контекст "
                        f"отправлены только последние {cap}. Старые повороты "
                        "не видны модели — для деталей зови recall_chat / get_message."
                    ),
                }
    except Exception:
        logger.exception("[pipeline] context warning compute failed (non-fatal)")

    if context_warning:
        await _emit("context_warning", context_warning)
        debug_trace["context_warning"] = context_warning

    # 12. Return response
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
        "context_warning": context_warning,
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


# The orchestrator above is the PipelineRun.run method (kept at module scope to
# avoid re-indenting the body; nested closures migrate to methods incrementally).
PipelineRun.run = _chat_completion_inner


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
    """Auto-generate chat title from first turn.

    provider may be None (e.g. Tier 0 path where no LLM was loaded).
    Falls back to using the first line of user_content as the title when
    the LLM summarise call fails or provider is unavailable.
    """
    try:
        from app.core.database import async_session
        from app.models.chat import Chat
        async with async_session() as db:
            chat = (await db.execute(select(Chat).where(Chat.id == chat_id))).scalar_one_or_none()
            if not chat or (chat.title and chat.description):
                return
            summary: str | None = None
            if provider is not None:
                try:
                    summary_model = _pick_summary_model_name(config, fallback_model_name)
                    language_hint = _detect_title_language(user_content)
                    summary = await provider.summarize(
                        f"User message:\n{user_content}\n\nAssistant response:\n{assistant_content}",
                        summary_model,
                        language_hint=language_hint,
                    )
                    summary = (summary or "").strip()[:200]
                except Exception:
                    logger.debug("LLM summarize failed, falling back to user content", exc_info=True)
            if not summary:
                # Fallback: use the first line of the user message, truncated.
                summary = (user_content or "").strip().split('\n')[0][:100].strip()
            if not summary:
                return
            if not chat.title:
                chat.title = summary
            if not chat.description:
                chat.description = summary
            await db.commit()
    except Exception:
        logger.debug("Background auto-summary failed", exc_info=True)


MEMORY_EXTRACTION_PROMPT = """Проанализируй диалог и извлеки ТОЛЬКО факты, которые НЕЛЬЗЯ восстановить через инструменты (tools) и которые будут полезны для БУДУЩИХ диалогов.

Верни ТОЛЬКО JSON-массив фактов. Каждый факт — объект:
- "fact": краткая формулировка (1 предложение, на языке диалога)
- "type": "long_term" (постоянная характеристика) или "episodic" (контекст текущей сессии)

ИЗВЛЕКАЙ:
- Предпочтения пользователя: как любит получать ответы, формат вывода, стиль общения.
- Решения и выводы, которые НЕ хранятся в БД (например: «объект X решено перенести», «задача Y приостановлена до пятницы»).
- Имя / роль пользователя, если он его сообщил («Меня зовут Иван, я администратор»).
- Названия проектов, зон ответственности или рабочих областей пользователя.

НЕ ИЗВЛЕКАЙ:
- Конкретные данные, которые живут в БД и доступны через tools: идентификаторы, адреса, номера, статусы, параметры объектов.
- Результаты разовых запросов / диагностики — они устаревают и бессмысленны вне контекста.
- Промежуточные шаги диалога, цитаты из ответов модели, обрывки tool-вывода.
- Общеизвестную информацию.

Если ничего из РАЗРЕШЁННОЙ категории нет — верни []. Не придумывай.

Диалог:
User: {user_message}
Assistant: {assistant_message}

JSON:"""

# Heuristics to drop low-value extracted facts that slipped through the prompt.
_MEMORY_BLOCK_PATTERNS = [
    r"\bMAC\b",
    r"IP\s*[-:]?\s*\d",
    r"\bport\b|\bпорт\s*\d",
    r"в\s+статусе\s+(forward|down|up|online|offline)",
    r"подключен\s+через",
    r"\bid\s*[:=]\s*\d",
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
            # Durable embed job — committed together with the entry (2445), so
            # the worker never races a not-yet-committed row.
            try:
                await enqueue_job(db, "embed_memory", {"memory_id": str(entry.id)}, tenant_id=tenant_id)
            except Exception:
                logger.debug("memory: failed to enqueue embed", exc_info=True)

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


def _estimate_round_tokens(msgs: list[dict], tool_defs_arg: list[dict] | None) -> int:
    """Approx prompt size at the moment of an LLM call — used by the auto-router
    to decide whether to escalate to the heavy model. Pure."""
    parts: list[str] = []
    for m in msgs:
        parts.append(_message_content_text(m.get("content", "")))
        tcs = m.get("tool_calls") or []
        if tcs:
            try:
                parts.append(json.dumps(tcs, ensure_ascii=False))
            except Exception:
                pass
    body = "\n".join(parts)
    tools_text = json.dumps(tool_defs_arg, ensure_ascii=False) if tool_defs_arg else ""
    return _ct(body) + _ct(tools_text)


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


def _build_prompt_layout(
    messages: list[dict],
    tool_defs: list[dict] | None,
    *,
    tool_mode: bool,
    system_block_labels: list[str] | None = None,
    system_block_contents: list[str] | None = None,
) -> dict:
    # Find the last user message — that's the "current request", even when
    # system tails (language reminder, etc) get appended after it.
    last_user_idx = -1
    for i, m in enumerate(messages):
        if m.get("role") == "user":
            last_user_idx = i

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
        elif role == "user" and idx == last_user_idx:
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

    # The first system message is a concatenation of independent blocks
    # (language pin, ontology, HARDCODED-*, BLOCK-MEMORY-*, BLOCK-KB, ...).
    # Expose them as a sibling array so the UI can render each block as
    # its own card with a "where this came from" label, instead of one
    # opaque megablob.
    system_blocks: list[dict] = []
    if system_block_labels and system_block_contents and len(system_block_labels) == len(system_block_contents):
        for label, content in zip(system_block_labels, system_block_contents):
            text = content or ""
            compact = _compact_text(text, max_chars=1600)
            system_blocks.append({
                "label": label,
                "chars": len(text),
                "est_tokens": _ct(text),
                "content": compact if compact else text[:1600],
            })

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
        "system_blocks": system_blocks,
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


def _tool_budget_for_model(model_name: str | None) -> int:
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


async def _select_relevant_tools(
    all_tools: list,
    user_message: str,
    provider,
    model_name: str,
    *,
    embedding_model: str | None = None,
    db = None,
    tenant_id: str | None = None,
    semantic_floor: float = 0.5,
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
    if has_enough_embeddings:
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

    # Tier 3 — keyword fallback (also used for small tenants without embeddings)
    if not selected and len(rest) <= TOOL_KEYWORD_THRESHOLD:
        try:
            selected = _keyword_match_tools(rest, user_message)
            for t in selected:
                t._selection_source = "keyword"
            selection_method = "keyword"
        except Exception:
            logger.exception("keyword tool selection failed")

    # Tier 4 — last resort, LLM picks from name+description list
    if not selected:
        try:
            selected = await _llm_select_tools(rest, user_message, provider, model_name)
            for t in selected:
                t._selection_source = "llm-pick"
            selection_method = "llm-pick"
        except Exception:
            selected = []
            selection_method = "fallback-empty"

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

    # Generic — works for any tool that declares which fields are selectable.
    selectable = runtime.get("selectable_fields")
    if isinstance(selectable, list) and selectable:
        _augment_selectable_fields_tool_definition(function_def, selectable)


def _augment_selectable_fields_tool_definition(function_def: dict, selectable_fields: list) -> None:
    """Inject a `fields` parameter into the tool schema so the LLM knows it
    can ask for only specific output columns. Executor post-filters the
    response to match.

    Skip if the tool already has a `fields` property (admin defined their
    own — don't clobber).
    """
    params = function_def.get("parameters")
    if not isinstance(params, dict):
        return
    props = params.get("properties")
    if not isinstance(props, dict):
        return
    if "fields" in props:
        return  # admin-defined; respect it
    clean = [str(f).strip() for f in selectable_fields if isinstance(f, str) and str(f).strip()]
    if not clean:
        return
    props["fields"] = {
        "type": "array",
        "items": {"type": "string", "enum": clean},
        "description": (
            "Опционально: список нужных полей результата. По умолчанию возвращаются ВСЕ. "
            "Указывай, когда тебе нужны только несколько колонок — это уменьшит размер "
            f"ответа в разы. Допустимые поля: {', '.join(clean[:20])}"
            + (", ..." if len(clean) > 20 else "")
        ),
    }


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
