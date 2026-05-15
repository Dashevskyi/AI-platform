"""
Per-tenant LLM request throttling.

Each tenant gets a TenantThrottle that bounds:
  - in-flight LLM pipeline runs (`max_concurrent`)
  - queued waiters (`max_queue`)

Two overflow policies:
  - reject_429:  if all slots busy → raise ThrottleRejected immediately
  - queue_fifo:  wait for a free slot; if queue depth exceeds `max_queue` → ThrottleRejected

Throttles are kept in a process-local registry keyed by tenant_id.
Settings can change at runtime via `update()`.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

logger = logging.getLogger(__name__)


class ThrottleRejected(Exception):
    """Raised when a tenant exceeds its concurrency/queue budget."""

    def __init__(self, message: str, retry_after_seconds: int = 5) -> None:
        super().__init__(message)
        self.retry_after = retry_after_seconds


class TenantThrottle:
    def __init__(self, max_concurrent: int, max_queue: int, overflow_policy: str) -> None:
        self.max_concurrent = max(1, int(max_concurrent))
        self.max_queue = max(0, int(max_queue))
        self.overflow_policy = overflow_policy if overflow_policy in {"reject_429", "queue_fifo"} else "reject_429"
        self._cv = asyncio.Condition()
        self._in_flight = 0
        self._waiting = 0

    def update(self, max_concurrent: int, max_queue: int, overflow_policy: str) -> None:
        self.max_concurrent = max(1, int(max_concurrent))
        self.max_queue = max(0, int(max_queue))
        self.overflow_policy = overflow_policy if overflow_policy in {"reject_429", "queue_fifo"} else "reject_429"
        # Wake everyone — some waiters may now fit
        async def _notify():
            async with self._cv:
                self._cv.notify_all()
        try:
            asyncio.get_event_loop().create_task(_notify())
        except RuntimeError:
            pass

    @property
    def stats(self) -> dict:
        return {
            "max_concurrent": self.max_concurrent,
            "max_queue": self.max_queue,
            "overflow_policy": self.overflow_policy,
            "in_flight": self._in_flight,
            "waiting": self._waiting,
        }

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        async with self._cv:
            if self._in_flight >= self.max_concurrent:
                # Need to wait or reject
                if self.overflow_policy == "reject_429":
                    raise ThrottleRejected(
                        f"Превышен лимит параллельных LLM-запросов ({self.max_concurrent}). Попробуйте через несколько секунд.",
                        retry_after_seconds=5,
                    )
                # queue_fifo
                if self._waiting >= self.max_queue:
                    raise ThrottleRejected(
                        f"Очередь заполнена ({self.max_queue}). Попробуйте через несколько секунд.",
                        retry_after_seconds=10,
                    )
                self._waiting += 1
                try:
                    while self._in_flight >= self.max_concurrent:
                        await self._cv.wait()
                finally:
                    self._waiting -= 1
            self._in_flight += 1
        try:
            yield
        finally:
            async with self._cv:
                self._in_flight -= 1
                self._cv.notify()


_REGISTRY: dict[str, TenantThrottle] = {}
_REGISTRY_LOCK = asyncio.Lock()


def _key(tenant_id: uuid.UUID | str) -> str:
    return str(tenant_id)


async def get_or_create_throttle(
    tenant_id: uuid.UUID | str,
    *,
    max_concurrent: int,
    max_queue: int,
    overflow_policy: str,
) -> TenantThrottle:
    k = _key(tenant_id)
    async with _REGISTRY_LOCK:
        existing = _REGISTRY.get(k)
        if existing is None:
            t = TenantThrottle(max_concurrent=max_concurrent, max_queue=max_queue, overflow_policy=overflow_policy)
            _REGISTRY[k] = t
            return t
        # Refresh settings if they changed
        if (
            existing.max_concurrent != max_concurrent
            or existing.max_queue != max_queue
            or existing.overflow_policy != overflow_policy
        ):
            existing.update(max_concurrent=max_concurrent, max_queue=max_queue, overflow_policy=overflow_policy)
        return existing


def all_stats() -> dict[str, dict]:
    return {k: t.stats for k, t in _REGISTRY.items()}
