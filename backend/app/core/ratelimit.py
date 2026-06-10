"""In-process sliding-window rate limiter (per client IP).

With 2 uvicorn workers the effective limit is up to 2× the configured value —
good enough for brute-force / resource-abuse protection, not for billing.
"""
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status


class RateLimiter:
    def __init__(self, max_requests: int, window_seconds: float = 60.0):
        self.max_requests = max_requests
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    @staticmethod
    def _client_ip(request: Request) -> str:
        # Behind nginx every request.client.host is 127.0.0.1 — use the first
        # X-Forwarded-For hop (set by our nginx) when present.
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def _prune(self, now: float) -> None:
        if len(self._hits) > 10_000:
            stale = [ip for ip, q in self._hits.items() if not q or now - q[-1] > self.window]
            for ip in stale:
                del self._hits[ip]

    async def __call__(self, request: Request) -> None:
        # Public traffic always arrives via nginx, which sets X-Forwarded-For.
        # A direct loopback call with no XFF is local/trusted (health checks,
        # tests, on-box scripts) — don't throttle it.
        if not request.headers.get("x-forwarded-for"):
            host = request.client.host if request.client else ""
            if host in ("127.0.0.1", "::1", "testclient", ""):
                return

        now = time.monotonic()
        ip = self._client_ip(request)
        q = self._hits[ip]
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.max_requests:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Слишком много запросов. Попробуйте позже.",
            )
        q.append(now)
        self._prune(now)


login_limiter = RateLimiter(max_requests=10, window_seconds=60.0)
voice_limiter = RateLimiter(max_requests=30, window_seconds=60.0)
