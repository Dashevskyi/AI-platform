"""Per-request shared context: one query embedding + stage timings + caches."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings as app_settings
from app.providers.factory import get_provider

logger = logging.getLogger(__name__)


@dataclass
class StageTimer:
    """Accumulates wall-clock ms per pipeline stage into timings_ms."""

    _starts: dict[str, float] = field(default_factory=dict)
    timings_ms: dict[str, int] = field(default_factory=dict)

    def start(self, name: str) -> None:
        self._starts[name] = time.perf_counter()

    def stop(self, name: str) -> None:
        t0 = self._starts.pop(name, None)
        if t0 is None:
            return
        self.timings_ms[name] = self.timings_ms.get(name, 0) + int((time.perf_counter() - t0) * 1000)

    def mark(self, name: str, ms: int) -> None:
        self.timings_ms[name] = self.timings_ms.get(name, 0) + ms


@dataclass
class RequestContext:
    """Shared per-turn state to avoid duplicate embed calls and tool searches."""

    query: str
    embedding_model: str | None = None
    query_vector: list[float] | None = None
    embed_ms: int = 0
    semantic_tools_cache: list[Any] = field(default_factory=list)
    ontology_examples: list[dict] = field(default_factory=list)
    preflight_reason: str = ""
    prompt_cache_key: str | None = None
    timer: StageTimer = field(default_factory=StageTimer)

    async def ensure_query_vector(self, db: AsyncSession | None = None) -> list[float] | None:
        if self.query_vector is not None:
            return self.query_vector
        if not self.embedding_model or not (self.query or "").strip():
            return None
        t0 = time.perf_counter()
        try:
            provider = get_provider("ollama", app_settings.OLLAMA_BASE_URL or "http://localhost:11434")
            vectors = await provider.embed(self.query, self.embedding_model)
            if vectors:
                self.query_vector = vectors[0]
        except Exception:
            logger.exception("request_context: query embed failed")
        self.embed_ms = int((time.perf_counter() - t0) * 1000)
        return self.query_vector


def load_ontology_examples(ontology_json: dict | None) -> list[dict]:
    """Extract example rows from structured ontology for routing boost."""
    if not isinstance(ontology_json, dict):
        return []
    out: list[dict] = []
    for sec in ontology_json.get("sections") or []:
        if not isinstance(sec, dict) or sec.get("type") != "examples":
            continue
        for item in sec.get("items") or []:
            if not isinstance(item, dict):
                continue
            q = str(
                item.get("query") or item.get("question") or item.get("text") or ""
            ).strip()
            et = str(item.get("expected_tool") or item.get("tool") or "").strip()
            if q and et:
                out.append({"query": q, "expected_tool": et})
    return out


def ontology_tool_boost(
    query: str,
    tool_name: str,
    examples: list[dict],
    *,
    full_bonus: float = 0.12,
    partial_bonus: float = 0.06,
) -> float:
    """Boost semantic score when query resembles an ontology example for this tool."""
    if not examples or not tool_name:
        return 0.0
    q = (query or "").lower().strip()
    if len(q) < 4:
        return 0.0
    best = 0.0
    for ex in examples:
        if ex.get("expected_tool") != tool_name:
            continue
        eq = str(ex.get("query") or "").lower().strip()
        if not eq:
            continue
        if q == eq or eq in q or q in eq:
            best = max(best, full_bonus)
        elif len(eq) >= 8 and (eq[:20] in q or q[:20] in eq):
            best = max(best, partial_bonus)
    return best
