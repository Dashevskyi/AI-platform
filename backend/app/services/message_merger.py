"""
Per-(chat, api_key) message merging.

When two or more user messages arrive within `merge_window_ms` for the
same (chat_id, api_key_id) tuple, they are buffered and processed by the
LLM pipeline as a single merged user_content. All HTTP waiters receive
the same final result and (for streaming) the same stream of events.

Buffer is debounced — every new arrival resets the timer.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

EventCallback = Callable[[str, dict], Awaitable[None]]


@dataclass
class _Buffer:
    tenant_id: str
    chat_id: str
    api_key_id: str | None
    message_ids: list[str] = field(default_factory=list)
    contents: list[str] = field(default_factory=list)
    event_callbacks: list[EventCallback] = field(default_factory=list)
    timer_task: asyncio.Task | None = None
    result_future: asyncio.Future = field(default_factory=lambda: asyncio.get_event_loop().create_future())


_BUFFERS: dict[tuple[str, str], _Buffer] = {}
_LOCK = asyncio.Lock()


def _key(chat_id: str, api_key_id: str | None) -> tuple[str, str]:
    return (str(chat_id), str(api_key_id) if api_key_id else "")


async def submit_or_merge(
    *,
    tenant_id: str,
    chat_id: str,
    api_key_id: str | None,
    user_message_id: str,
    content: str,
    on_event: EventCallback | None,
    merge_window_ms: int,
) -> dict:
    """
    Submit a user message into the per-(chat, key) buffer.
    Returns the chat_completion dict result once the merged batch is processed.
    """
    if merge_window_ms <= 0:
        # Edge case: window=0 means no merging. Caller should handle.
        raise ValueError("merge_window_ms must be > 0")

    key = _key(chat_id, api_key_id)

    async with _LOCK:
        buf = _BUFFERS.get(key)
        if buf is None:
            buf = _Buffer(tenant_id=str(tenant_id), chat_id=str(chat_id), api_key_id=api_key_id)
            _BUFFERS[key] = buf
        buf.message_ids.append(str(user_message_id))
        buf.contents.append(content)
        if on_event is not None:
            buf.event_callbacks.append(on_event)
        # Debounce: cancel old timer and schedule a new one
        if buf.timer_task is not None and not buf.timer_task.done():
            buf.timer_task.cancel()
        buf.timer_task = asyncio.create_task(_fire_after(key, merge_window_ms / 1000.0))
        future = buf.result_future

    # Notify all waiters that buffering is happening (so the UI knows)
    if on_event:
        await on_event("merge_pending", {
            "window_ms": merge_window_ms,
            "buffered_count": len(buf.message_ids),
        })

    return await future


async def _fire_after(key: tuple[str, str], delay_seconds: float) -> None:
    try:
        await asyncio.sleep(delay_seconds)
    except asyncio.CancelledError:
        return  # debounced — newer task will take over

    async with _LOCK:
        buf = _BUFFERS.pop(key, None)
    if buf is None:
        return

    # Run the pipeline ONCE with merged content. Use a fresh DB session
    # because each waiter's request may have already committed/closed theirs.
    from app.core.database import async_session
    from app.services.llm.pipeline import chat_completion
    from app.services.throttle import ThrottleRejected
    from app.models.message import Message

    merged_content = "\n\n".join(buf.contents).strip()
    last_message_id = buf.message_ids[-1]
    trail: list[dict] = []
    TRAIL_KEEP = {
        "kb_search_start", "kb_search_done",
        "provider_call_start", "provider_call_done",
        "tool_call_start", "tool_call_done",
        "reasoning", "error",
    }

    async def fanout_emitter(event_type: str, payload: dict) -> None:
        if event_type in TRAIL_KEEP:
            trail.append({"type": event_type, "payload": payload})
        if not buf.event_callbacks:
            return
        await asyncio.gather(
            *(_safe_call(cb, event_type, payload) for cb in buf.event_callbacks),
            return_exceptions=True,
        )

    try:
        async with async_session() as db:
            await fanout_emitter("merge_start", {
                "merged_count": len(buf.message_ids),
                "merged_content_chars": len(merged_content),
            })
            llm_result = await chat_completion(
                tenant_id=buf.tenant_id,
                chat_id=buf.chat_id,
                user_content=merged_content,
                db=db,
                user_message_id=last_message_id,
                api_key_id=buf.api_key_id,
                on_event=fanout_emitter,
                merged_message_ids=buf.message_ids,
            )
            assistant_metadata = {
                "time_to_first_token_ms": llm_result.get("time_to_first_token_ms"),
                "provider_type": llm_result.get("provider_type"),
                "model_name": llm_result.get("model_name"),
                "correlation_id": llm_result.get("correlation_id"),
                "reasoning": llm_result.get("reasoning"),
                "events": trail,
                "tool_calls_count": llm_result.get("tool_calls_count"),
                "finish_reason": llm_result.get("finish_reason"),
                "response_summary": llm_result.get("response_summary"),
                "tool_result_summary": llm_result.get("tool_result_summary"),
                "attachment_summary": llm_result.get("attachment_summary"),
                "context_card": llm_result.get("context_card"),
                "history_exclude": llm_result.get("history_exclude"),
                "merged_message_ids": buf.message_ids,
                "merged_count": len(buf.message_ids),
            }
            assistant_message = Message(
                tenant_id=uuid.UUID(buf.tenant_id),
                chat_id=uuid.UUID(buf.chat_id),
                role="assistant",
                content=llm_result.get("content", ""),
                metadata_json=assistant_metadata,
                prompt_tokens=llm_result.get("prompt_tokens"),
                completion_tokens=llm_result.get("completion_tokens"),
                total_tokens=llm_result.get("total_tokens"),
                latency_ms=llm_result.get("latency_ms"),
                status="sent",
            )
            db.add(assistant_message)
            await db.flush()
            await db.commit()
            await db.refresh(assistant_message)
        if not buf.result_future.done():
            buf.result_future.set_result({
                "assistant_message_id": str(assistant_message.id),
                "content": llm_result.get("content", ""),
                "metadata": assistant_metadata,
                "llm_result": llm_result,
            })
    except ThrottleRejected as exc:
        if not buf.result_future.done():
            buf.result_future.set_exception(exc)
    except Exception as exc:
        logger.exception("Merger pipeline failed for %s", key)
        if not buf.result_future.done():
            buf.result_future.set_exception(exc)


async def _safe_call(cb: EventCallback, event_type: str, payload: dict) -> None:
    try:
        await cb(event_type, payload)
    except Exception:
        logger.warning("merge fan-out callback raised", exc_info=True)
