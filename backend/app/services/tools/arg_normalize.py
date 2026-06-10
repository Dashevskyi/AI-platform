"""
Profile-driven argument normalization for tools whose backend wants a
specific input format depending on some upstream attribute (e.g. hardware
vendor, account currency, locale).

The executor stays tenant-agnostic: this module knows only about generic
mechanics — SQL lookup → match a key against profiles → apply registered
format converters. No tenant names, no vendor names, no tool names live
here. Tenants opt in by populating `config_json.arg_normalize` on their
tool; the same shape works for any tool whose backend formatting depends
on a per-record attribute.

Tool config shape (lives in `tenant_tools.config_json`, NOT in code):

    "arg_normalize": {
        "lookup": {
            "data_source_id": "uuid-of-tenant-datasource",
            "sql": "SELECT some_col FROM some_tbl WHERE pk = :foo",
            "param_from": "foo",         # arg name whose value is the SQL param
            "result_field": "some_col",  # column to read from row 0
            "cache_seconds": 300         # in-process cache TTL
        },
        "profiles": [
            # `match` is checked with case-insensitive substring inside the
            # value returned by `lookup` (a.k.a. the profile key). First match wins.
            { "match": "<keyA>", "mac": "dotted4_lower", "interface": "lower" },
            { "match": "<keyB>", "mac": "colon_lower",  "interface": "upper" }
        ],
        "fields": {
            # Dotted path inside `arguments` → conversion kind. Kind names
            # come from the converters registry below.
            "query_params.mac": "mac",
            "query_params.interface": "interface",
            "query_params.port_number": "port_number"
        }
    }

Built-in conversion kinds (registry-driven; tenants requesting new domains
can add converters here without touching the executor itself):
  mac:        colon_lower, colon_upper, dash_lower, dash_upper,
              dotted4_lower, dotted4_upper, nosep_lower, nosep_upper
  interface:  lower, upper, original (no change)
  port_number: as_int, as_str
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Generic format converters — domain-neutral utility library
# ──────────────────────────────────────────────────────────────────────

_MAC_HEX_RE = re.compile(r"[0-9a-fA-F]")


def _mac_hex_only(value: str) -> str | None:
    digits = "".join(_MAC_HEX_RE.findall(value or ""))
    if len(digits) != 12:
        return None
    return digits


def normalize_mac(value: Any, fmt: str) -> Any:
    """Normalize a MAC value. Returns the original value unchanged if it
    doesn't look like a MAC (12 hex digits after stripping separators) or
    if `fmt` is unknown — we never raise from normalization, the schema
    validator will catch genuine garbage later."""
    if not isinstance(value, str):
        return value
    digits = _mac_hex_only(value)
    if digits is None:
        return value
    case = "lower" if fmt.endswith("lower") else "upper"
    d = digits.lower() if case == "lower" else digits.upper()
    if fmt.startswith("colon"):
        return ":".join(d[i:i + 2] for i in range(0, 12, 2))
    if fmt.startswith("dash"):
        return "-".join(d[i:i + 2] for i in range(0, 12, 2))
    if fmt.startswith("dotted4"):
        return ".".join(d[i:i + 4] for i in range(0, 12, 4))
    if fmt.startswith("nosep"):
        return d
    return value


def normalize_interface(value: Any, fmt: str) -> Any:
    if not isinstance(value, str):
        return value
    if fmt == "lower":
        return value.lower()
    if fmt == "upper":
        return value.upper()
    return value


def normalize_port_number(value: Any, fmt: str) -> Any:
    if fmt == "as_int":
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return value
    if fmt == "as_str":
        if value is None:
            return value
        return str(value)
    return value


_KIND_DISPATCH = {
    "mac": normalize_mac,
    "interface": normalize_interface,
    "port_number": normalize_port_number,
}


# ──────────────────────────────────────────────────────────────────────
# In-process lookup cache: {(data_source_id, sql, param_value) -> (profile_key, expires_at)}
# Keeps the lookup off the DB when several calls in one conversation hit
# the same record (e.g. five tool calls against the same switch_id).
# ──────────────────────────────────────────────────────────────────────

@dataclass
class _CacheEntry:
    profile_key: str | None
    expires_at: float


_LOOKUP_CACHE: dict[tuple[str, str, str], _CacheEntry] = {}


def _cache_get(key: tuple[str, str, str]) -> tuple[str | None, bool]:
    entry = _LOOKUP_CACHE.get(key)
    if entry is None:
        return (None, False)
    if entry.expires_at < time.time():
        _LOOKUP_CACHE.pop(key, None)
        return (None, False)
    return (entry.profile_key, True)


def _cache_put(key: tuple[str, str, str], profile_key: str | None, ttl: float) -> None:
    _LOOKUP_CACHE[key] = _CacheEntry(profile_key=profile_key, expires_at=time.time() + ttl)


# ──────────────────────────────────────────────────────────────────────
# Path utilities
# ──────────────────────────────────────────────────────────────────────

def _get_nested(obj: Any, path: str) -> Any:
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _set_nested(obj: Any, path: str, value: Any) -> bool:
    parts = path.split(".")
    cur = obj
    for part in parts[:-1]:
        if not isinstance(cur, dict) or part not in cur:
            return False
        cur = cur[part]
    if not isinstance(cur, dict):
        return False
    cur[parts[-1]] = value
    return True


def _match_profile(profile_key: str, profiles: list[dict]) -> dict | None:
    key_l = (profile_key or "").lower()
    for p in profiles or []:
        match = str(p.get("match") or "").lower().strip()
        if not match:
            continue
        if match in key_l:
            return p
    return None


# ──────────────────────────────────────────────────────────────────────
# Public API used by executor
# ──────────────────────────────────────────────────────────────────────

async def lookup_profile_key(
    lookup_cfg: dict,
    arguments: dict,
    *,
    fetch_sql_rows,  # callable: (db_url, sql, params) -> list[dict]
    resolve_db_url,  # callable: (data_source_id) -> str (awaitable)
) -> tuple[str | None, str | None]:
    """Resolve the profile-selector string (e.g. a vendor name) for the
    current tool invocation by running the configured SQL lookup against
    the tenant data source.

    Returns (profile_key, error). On error, profile_key is None and error
    is a human-readable string (Russian) safe to surface to the model
    as a tool error.
    """
    data_source_id = str(lookup_cfg.get("data_source_id") or "").strip()
    sql = str(lookup_cfg.get("sql") or "").strip()
    param_from = str(lookup_cfg.get("param_from") or "").strip()
    result_field = str(lookup_cfg.get("result_field") or "").strip()
    ttl = float(lookup_cfg.get("cache_seconds") or 300)

    if not (data_source_id and sql and param_from and result_field):
        return None, "arg_normalize.lookup: не хватает data_source_id/sql/param_from/result_field"

    param_value = _get_nested(arguments, param_from)
    if param_value in (None, ""):
        return None, f"arg_normalize.lookup: в аргументах нет '{param_from}' для подстановки в SQL"
    param_value_str = str(param_value)

    cache_key = (data_source_id, sql, param_value_str)
    cached, hit = _cache_get(cache_key)
    if hit:
        return cached, None

    try:
        db_url = await resolve_db_url(data_source_id)
    except Exception as e:
        return None, f"arg_normalize.lookup: data_source не разрешается: {str(e)[:200]}"

    try:
        rows = await fetch_sql_rows(db_url, sql, {param_from: param_value})
    except Exception as e:
        return None, f"arg_normalize.lookup SQL ошибка: {str(e)[:200]}"

    if not rows:
        _cache_put(cache_key, None, ttl)
        return None, f"arg_normalize.lookup: запись не найдена ({param_from}={param_value_str})"

    raw = rows[0].get(result_field)
    key = str(raw).strip() if raw is not None else None
    _cache_put(cache_key, key, ttl)
    return key, None


def apply_profile(arguments: dict, profile: dict, field_map: dict) -> list[str]:
    """Apply a single vendor profile to arguments in-place. Returns a list
    of human-readable transformation notes for debug/telemetry."""
    notes: list[str] = []
    for arg_path, kind in (field_map or {}).items():
        kind_str = str(kind).strip()
        fmt = profile.get(kind_str)
        if not fmt or not isinstance(fmt, str):
            continue
        converter = _KIND_DISPATCH.get(kind_str)
        if converter is None:
            continue
        current = _get_nested(arguments, arg_path)
        if current is None:
            continue
        new_value = converter(current, fmt)
        if new_value != current and _set_nested(arguments, arg_path, new_value):
            notes.append(f"{arg_path}: {current!r} → {new_value!r} ({kind_str}/{fmt})")
    return notes


async def normalize_arguments(
    tool_name: str,
    arguments: dict,
    tool_config: dict | None,
    *,
    fetch_sql_rows,
    resolve_db_url,
) -> tuple[list[str], str | None]:
    """Mutates `arguments` in place. Returns (notes, error). When error is
    not None, the caller should abort the tool call and surface the error
    to the model."""
    if not isinstance(tool_config, dict):
        return [], None
    block = tool_config.get("arg_normalize")
    if not isinstance(block, dict):
        return [], None

    profiles = block.get("profiles") if isinstance(block.get("profiles"), list) else []
    fields = block.get("fields") if isinstance(block.get("fields"), dict) else {}
    lookup_cfg = block.get("lookup") if isinstance(block.get("lookup"), dict) else None
    if not profiles or not fields or not lookup_cfg:
        return [], None

    profile_key, err = await lookup_profile_key(
        lookup_cfg,
        arguments,
        fetch_sql_rows=fetch_sql_rows,
        resolve_db_url=resolve_db_url,
    )
    if err:
        return [], err
    if not profile_key:
        return [], None  # no key found — leave args untouched

    profile = _match_profile(profile_key, profiles)
    if profile is None:
        logger.debug(
            "[arg_normalize] %s: profile_key=%r — no matching profile, args untouched",
            tool_name, profile_key,
        )
        return [], None

    notes = apply_profile(arguments, profile, fields)
    if notes:
        logger.info(
            "[arg_normalize] %s profile_key=%r profile.match=%r changes: %s",
            tool_name, profile_key, profile.get("match"), "; ".join(notes),
        )
    return notes, None
