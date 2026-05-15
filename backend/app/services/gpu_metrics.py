"""GPU metrics aggregator.

Scrapes the host nvidia_gpu_exporter (:9835) and vLLM (:8000/metrics),
parses Prometheus text format, returns a unified snapshot.

Used by the admin GPU dashboard and the background worker that persists
snapshots for historical charts.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import delete, select

from app.core.database import async_session
from app.models.gpu_metric_snapshot import GPUMetricSnapshot

logger = logging.getLogger(__name__)

GPU_EXPORTER_URL = os.getenv("GPU_EXPORTER_URL", "http://172.10.100.9:9835/metrics")
VLLM_METRICS_URL = os.getenv("VLLM_METRICS_URL", "http://172.10.100.9:8000/metrics")
SNAPSHOT_INTERVAL_SEC = int(os.getenv("GPU_SNAPSHOT_INTERVAL_SEC", "10"))
SNAPSHOT_RETENTION_DAYS = int(os.getenv("GPU_SNAPSHOT_RETENTION_DAYS", "7"))
CACHE_TTL_SEC = 2.5

# Prometheus sample line:  metric{label="x",label="y"} VALUE [timestamp]
_SAMPLE_RE = re.compile(r"^(?P<name>[a-zA-Z_][a-zA-Z0-9_:]*)(?:\{(?P<labels>[^}]*)\})?\s+(?P<value>\S+)")
_LABEL_RE = re.compile(r'(\w+)="([^"]*)"')


def _parse_prometheus(text: str) -> dict[str, list[tuple[dict[str, str], float]]]:
    out: dict[str, list[tuple[dict[str, str], float]]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _SAMPLE_RE.match(line)
        if not m:
            continue
        try:
            value = float(m.group("value"))
        except ValueError:
            continue
        labels: dict[str, str] = {}
        if m.group("labels"):
            for lm in _LABEL_RE.finditer(m.group("labels")):
                labels[lm.group(1)] = lm.group(2)
        out.setdefault(m.group("name"), []).append((labels, value))
    return out


def _first_for_uuid(samples: list[tuple[dict[str, str], float]] | None, uuid_: str) -> float | None:
    if not samples:
        return None
    for labels, value in samples:
        if labels.get("uuid") == uuid_:
            return value
    return None


def _first(samples: list[tuple[dict[str, str], float]] | None) -> float | None:
    if not samples:
        return None
    return samples[0][1]


async def _scrape(url: str, timeout: float = 3.0) -> dict[str, list[tuple[dict[str, str], float]]]:
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.get(url)
        r.raise_for_status()
        return _parse_prometheus(r.text)


def _build_gpu_list(metrics: dict[str, list[tuple[dict[str, str], float]]]) -> list[dict[str, Any]]:
    uuids: dict[str, dict[str, Any]] = {}
    for labels, _ in metrics.get("nvidia_smi_memory_total_bytes", []):
        u = labels.get("uuid")
        if u:
            uuids[u] = {
                "uuid": u,
                "name": labels.get("name") or labels.get("gpu_name") or "NVIDIA GPU",
                "idx": int(labels.get("index", "0") or 0),
            }
    if not uuids:
        return []

    for u, row in uuids.items():
        row["memory_total_bytes"] = _first_for_uuid(metrics.get("nvidia_smi_memory_total_bytes"), u)
        row["memory_used_bytes"] = _first_for_uuid(metrics.get("nvidia_smi_memory_used_bytes"), u)
        row["util_pct"] = (_first_for_uuid(metrics.get("nvidia_smi_utilization_gpu_ratio"), u) or 0.0) * 100
        row["util_memory_pct"] = (_first_for_uuid(metrics.get("nvidia_smi_utilization_memory_ratio"), u) or 0.0) * 100
        row["temperature_c"] = _first_for_uuid(metrics.get("nvidia_smi_temperature_gpu"), u)
        row["power_w"] = _first_for_uuid(metrics.get("nvidia_smi_power_draw_watts"), u)

    return sorted(uuids.values(), key=lambda g: g["idx"])


def _build_vllm_dict(metrics: dict[str, list[tuple[dict[str, str], float]]]) -> dict[str, Any] | None:
    if not metrics:
        return None
    return {
        "running": int(_first(metrics.get("vllm:num_requests_running")) or 0),
        "waiting": int(_first(metrics.get("vllm:num_requests_waiting")) or 0),
        "kv_cache_usage": _first(metrics.get("vllm:kv_cache_usage_perc")) or _first(metrics.get("vllm:gpu_cache_usage_perc")),
        "prompt_tokens_total": int(_first(metrics.get("vllm:prompt_tokens_total")) or 0),
        "generation_tokens_total": int(_first(metrics.get("vllm:generation_tokens_total")) or 0),
        "prefix_cache_hit_rate": _first(metrics.get("vllm:gpu_prefix_cache_hit_rate")),
        "scrape_ts": time.time(),
    }


_cache: dict[str, Any] = {"data": None, "ts": 0.0}


async def fetch_live_snapshot() -> dict[str, Any]:
    """Live snapshot with short TTL cache so the dashboard polling doesn't hammer exporters."""
    if _cache["data"] is not None and (time.time() - _cache["ts"]) < CACHE_TTL_SEC:
        return _cache["data"]

    gpu_metrics: dict[str, list[tuple[dict[str, str], float]]] = {}
    vllm_metrics: dict[str, list[tuple[dict[str, str], float]]] = {}
    try:
        gpu_metrics = await _scrape(GPU_EXPORTER_URL)
    except Exception as e:
        logger.warning("gpu exporter scrape failed: %s", e)
    try:
        vllm_metrics = await _scrape(VLLM_METRICS_URL)
    except Exception as e:
        logger.warning("vllm metrics scrape failed: %s", e)

    snapshot = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "gpus": _build_gpu_list(gpu_metrics),
        "vllm": _build_vllm_dict(vllm_metrics),
    }
    _cache["data"] = snapshot
    _cache["ts"] = time.time()
    return snapshot


async def _persist_one(prev_totals: dict[str, int]) -> None:
    snap = await fetch_live_snapshot()
    if not snap["gpus"]:
        return

    # Derive tokens_per_sec from delta of prompt+generation totals between ticks
    vllm = dict(snap.get("vllm") or {})
    if vllm:
        gen_total = vllm.get("generation_tokens_total") or 0
        prev = prev_totals.get("generation_tokens_total", gen_total)
        elapsed = max(SNAPSHOT_INTERVAL_SEC, 1)
        vllm["generation_tps"] = max(0.0, (gen_total - prev) / elapsed)
        prev_totals["generation_tokens_total"] = gen_total

    async with async_session() as db:
        db.add(GPUMetricSnapshot(gpus=snap["gpus"], vllm=vllm or None))
        await db.commit()


async def _purge_old() -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=SNAPSHOT_RETENTION_DAYS)
    async with async_session() as db:
        await db.execute(delete(GPUMetricSnapshot).where(GPUMetricSnapshot.created_at < cutoff))
        await db.commit()


async def snapshot_worker() -> None:
    """Background task — run every SNAPSHOT_INTERVAL_SEC."""
    logger.info("gpu snapshot worker started (interval=%ss, retention=%sd)",
                SNAPSHOT_INTERVAL_SEC, SNAPSHOT_RETENTION_DAYS)
    prev_totals: dict[str, int] = {}
    purge_counter = 0
    while True:
        try:
            await _persist_one(prev_totals)
        except Exception:
            logger.exception("gpu snapshot persist failed")
        purge_counter += 1
        if purge_counter * SNAPSHOT_INTERVAL_SEC >= 3600:  # purge once per hour
            try:
                await _purge_old()
            except Exception:
                logger.exception("gpu snapshot purge failed")
            purge_counter = 0
        await asyncio.sleep(SNAPSHOT_INTERVAL_SEC)


async def fetch_history(range_seconds: int) -> list[dict[str, Any]]:
    """Return downsampled timeseries. Postgres-side downsample via NTILE."""
    since = datetime.now(timezone.utc) - timedelta(seconds=range_seconds)
    async with async_session() as db:
        result = await db.execute(
            select(GPUMetricSnapshot)
            .where(GPUMetricSnapshot.created_at >= since)
            .order_by(GPUMetricSnapshot.created_at.asc())
        )
        rows = result.scalars().all()
    # Limit to ~200 points so the chart stays responsive
    if len(rows) > 200:
        step = max(1, len(rows) // 200)
        rows = rows[::step]
    return [
        {
            "ts": r.created_at.isoformat(),
            "gpus": r.gpus,
            "vllm": r.vllm,
        }
        for r in rows
    ]
