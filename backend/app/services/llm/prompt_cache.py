"""Stable prefix fingerprint for provider-side KV / prompt caching."""
from __future__ import annotations

import hashlib


def compute_prompt_cache_key(parts: list[str]) -> str | None:
    """SHA-256 of static system prefix blocks (first 32 hex chars)."""
    blob = "\n\n---BLOCK---\n\n".join(p.strip() for p in parts if p and str(p).strip())
    if not blob:
        return None
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def cache_extra_body(cache_key: str | None) -> dict:
    """Provider-agnostic hint; ignored by APIs that don't support it."""
    if not cache_key:
        return {}
    return {
        "metadata": {"prompt_cache_key": cache_key},
        "prompt_cache_key": cache_key,
    }
