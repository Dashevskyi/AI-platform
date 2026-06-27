"""Parallel tool execution within one LLM round."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.llm.pipeline import ToolExecCtx

logger = logging.getLogger(__name__)


@dataclass
class ToolCallOutcome:
    tool_call_id: str
    func_name: str
    func_args: dict
    tool_output: str
    tool_ok: bool
    latency_ms: int


async def _execute_tool_core(ctx: "ToolExecCtx", tc, round_num: int) -> ToolCallOutcome | None:
    from app.services.llm.pipeline import _parse_tool_call
    from app.services.tools.executor import execute_tool
    from app.core.config import settings as app_settings
    from app.providers.factory import get_provider

    parsed = _parse_tool_call(tc)
    if parsed is None:
        return None
    tool_call_id, func_name, func_args = parsed

    if func_name not in ctx.allowed_tool_names:
        return ToolCallOutcome(
            tool_call_id=tool_call_id,
            func_name=func_name,
            func_args=func_args,
            tool_output=f"Ошибка: инструмент '{func_name}' недоступен для этого tenant.",
            tool_ok=False,
            latency_ms=0,
        )

    await ctx.emit("tool_call_start", {
        "name": func_name,
        "round": round_num,
        "args_preview": json.dumps(func_args, ensure_ascii=False)[:300],
    })
    tool_t0 = time.time()
    if func_name in ctx.attachment_map:
        from app.services.attachments.tool import execute_attachment_search
        att_embed_provider = get_provider("ollama", app_settings.OLLAMA_BASE_URL or "http://localhost:11434")
        tool_output = await execute_attachment_search(
            attachment_id=ctx.attachment_map[func_name],
            query=func_args.get("query", ""),
            db=ctx.db,
            provider=att_embed_provider,
            embedding_model=ctx.config.embedding_model_name or "nomic-embed-text",
        )
        tool_ok = True
    else:
        _cfg = ctx.tool_config_map.get(func_name) or ctx.all_allowed_tools_for_tenant.get(func_name)
        if _cfg is not None and func_name not in ctx.tool_config_map:
            ctx.tool_config_map[func_name] = _cfg
            if ctx.tool_defs is not None and _cfg.get("function"):
                ctx.tool_defs.append({"type": _cfg.get("type", "function"), "function": _cfg["function"]})
        result = await execute_tool(func_name, func_args, _cfg)
        tool_ok = result.success
        tool_output = result.output if result.success else f"Ошибка: {result.error}"

    return ToolCallOutcome(
        tool_call_id=tool_call_id,
        func_name=func_name,
        func_args=func_args,
        tool_output=tool_output,
        tool_ok=tool_ok,
        latency_ms=int((time.time() - tool_t0) * 1000),
    )


async def _apply_tool_outcome(ctx: "ToolExecCtx", outcome: ToolCallOutcome, round_num: int) -> None:
    from app.services.llm.pipeline import _schedule_tool_result_capture, _ct

    logger.debug(
        "[%s] Tool result (%s, %d chars): %s",
        ctx.correlation_id, outcome.func_name, len(outcome.tool_output or ""), (outcome.tool_output or "")[:200],
    )
    await ctx.emit("tool_call_done", {
        "name": outcome.func_name,
        "round": round_num,
        "ok": outcome.tool_ok,
        "latency_ms": outcome.latency_ms,
        "output_chars": len(outcome.tool_output or ""),
        "output_tokens": _ct(outcome.tool_output or ""),
    })
    ctx.debug_tool_calls.append({
        "round": round_num,
        "name": outcome.func_name,
        "args_preview": json.dumps(outcome.func_args, ensure_ascii=False)[:300],
        "ok": outcome.tool_ok,
        "latency_ms": outcome.latency_ms,
        "output_chars": len(outcome.tool_output or ""),
    })
    ctx.tool_outputs.append({"tool": outcome.func_name, "output": outcome.tool_output})
    if ctx.tool_call_counts is not None:
        ctx.tool_call_counts[outcome.func_name] = ctx.tool_call_counts.get(outcome.func_name, 0) + 1
    if not outcome.tool_ok and ctx.failed_calls is not None:
        ctx.failed_calls[0] += 1
    if outcome.tool_ok:
        _schedule_tool_result_capture(
            ctx.capture_tasks_by_round, round_num,
            tenant_id=ctx.tenant_id, chat_id=ctx.chat_id, user_message_id=ctx.user_message_id,
            tool_name=outcome.func_name, arguments=outcome.func_args, output=outcome.tool_output or "",
            correlation_id=ctx.correlation_id,
        )
    ctx.messages.append(
        ctx.provider.format_tool_result_turn(
            tool_call_id=outcome.tool_call_id,
            content=outcome.tool_output,
        )
    )


async def execute_tool_calls(ctx: "ToolExecCtx", tool_calls: list, round_num: int) -> None:
    """Run tool calls in parallel when safe; preserve result order for the LLM."""
    if not tool_calls:
        return
    if len(tool_calls) == 1:
        outcome = await _execute_tool_core(ctx, tool_calls[0], round_num)
        if outcome:
            await _apply_tool_outcome(ctx, outcome, round_num)
        return

    outcomes: list[ToolCallOutcome | None] = await asyncio.gather(
        *[_execute_tool_core(ctx, tc, round_num) for tc in tool_calls]
    )
    for outcome in outcomes:
        if outcome:
            await _apply_tool_outcome(ctx, outcome, round_num)
