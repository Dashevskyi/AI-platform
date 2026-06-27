"""Helpers extracted from pipeline: history batching and parallel preflight."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.message import Message

logger = logging.getLogger(__name__)


async def batch_assistant_for_user_messages(
    db: AsyncSession,
    chat_id,
    user_rows: list[Message],
) -> list[tuple[Message, Message | None]]:
    """Map each user message to the next assistant reply — one query, no N+1."""
    if not user_rows:
        return []
    min_ts = min(u.created_at for u in user_rows if u.created_at is not None)
    assistants = list(
        (
            await db.execute(
                select(Message)
                .where(
                    Message.chat_id == chat_id,
                    Message.role == "assistant",
                    Message.created_at >= min_ts,
                )
                .order_by(Message.created_at.asc())
            )
        ).scalars().all()
    )
    pairs: list[tuple[Message, Message | None]] = []
    for u in user_rows:
        u_ts = u.created_at
        asst = None
        for a in assistants:
            if a.created_at is not None and u_ts is not None and a.created_at >= u_ts:
                asst = a
                break
        pairs.append((u, asst))
    return pairs


async def run_prefetch_parallel(
    *coros: Any,
    names: list[str] | None = None,
) -> list[Any]:
    """Run independent prefetch coroutines; log failures per task."""
    if not coros:
        return []
    results = await asyncio.gather(*coros, return_exceptions=True)
    out: list[Any] = []
    for i, r in enumerate(results):
        label = (names[i] if names and i < len(names) else f"task_{i}")
        if isinstance(r, Exception):
            logger.exception("[prefetch] %s failed (non-fatal)", label)
            out.append(None)
        else:
            out.append(r)
    return out
