"""
Tool executor — sandboxed server-side execution of tenant tools.

Each tool is a registered handler function with:
- Input validation
- Timeout
- No arbitrary code execution
"""
import asyncio
import json
import ipaddress
import logging
import re
import shlex
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import settings
from app.core.security import decrypt_value

logger = logging.getLogger(__name__)

# 30s — covers CPU-Ollama nomic-embed-text calls (find_artifacts, recall_chat)
# under load. Individual tools can override via tool_config.timeout_seconds.
TOOL_TIMEOUT_SECONDS = 30
PING_BATCH_TIMEOUT_SECONDS = 60
MAX_TOOL_TIMEOUT_SECONDS = 120
API_BATCH_CONCURRENCY = 10  # max parallel HTTP calls in a batch-expanded fetch_api_data

# Shared HTTP client — connection pool is reused across tool calls instead of
# opening a fresh TCP/TLS connection per request. Timeout is passed per request.
_http_client: httpx.AsyncClient | None = None


def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            follow_redirects=True,
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
        )
    return _http_client


@dataclass
class ToolResult:
    success: bool
    output: str
    error: str | None = None


# ============================================================
# Registry of built-in tool handlers
# ============================================================
_HANDLERS: dict[str, callable] = {}
_DB_ENGINES: dict[str, AsyncEngine] = {}


def register_tool(name: str):
    """Decorator to register a tool handler."""
    def decorator(func):
        _HANDLERS[name] = func
        return func
    return decorator


def get_available_tools() -> list[str]:
    return list(_HANDLERS.keys())


def resolve_tool_handler(tool_name: str, tool_config: dict | None = None) -> str:
    runtime = _extract_runtime_config(tool_config)
    handler = runtime.get("handler") or runtime.get("builtin_handler")
    if isinstance(handler, str) and handler.strip():
        return handler.strip()
    return tool_name


def _validate_arguments_against_schema(
    tool_name: str,
    arguments: dict,
    tool_config: dict | None,
) -> str | None:
    """Validate model-supplied `arguments` against the tool's declared JSON
    schema (config_json.function.parameters). Returns None on success, or a
    human-readable error string (in Russian, to be returned to the model) on
    failure.

    We don't run the schema's `required` check strictly here — many of our
    tools have empty `required` arrays and rely on tool-side defaults. We
    only enforce TYPE correctness, which is the actual failure mode we're
    seeing in production (string '1-18' where integer expected).
    """
    if not isinstance(tool_config, dict):
        return None
    fn = tool_config.get("function")
    if not isinstance(fn, dict):
        return None
    schema = fn.get("parameters")
    if not isinstance(schema, dict) or not schema.get("properties"):
        return None

    # Strip:
    #  - private fields (_context) — our infra, not part of model-facing schema
    #  - `fields` — runtime-injected by `_augment_selectable_fields...` for
    #    tools that opt into LLM-side column projection. Its values are
    #    enforced by the post-filter (unknown names silently ignored), not
    #    by the original tool schema.
    runtime = tool_config.get("x_backend_config") if isinstance(tool_config, dict) else None
    has_selectable = (
        isinstance(runtime, dict)
        and isinstance(runtime.get("selectable_fields"), list)
        and bool(runtime["selectable_fields"])
    )
    payload = {
        k: v for k, v in (arguments or {}).items()
        if not str(k).startswith("_")
        and not (k == "fields" and has_selectable)
    }

    try:
        from jsonschema import Draft202012Validator, ValidationError
    except ImportError:
        return None  # jsonschema not installed; skip silently

    # Build a relaxed schema: drop the top-level `required` (we don't enforce
    # required-arg here — that's the tool handler's call) but keep types and
    # nested constraints. Recursively walk and remove `required` arrays.
    def _strip_required(node):
        if isinstance(node, dict):
            node.pop("required", None)
            for v in node.values():
                _strip_required(v)
        elif isinstance(node, list):
            for v in node:
                _strip_required(v)

    from copy import deepcopy
    schema_for_validation = deepcopy(schema)
    _strip_required(schema_for_validation)

    validator = Draft202012Validator(schema_for_validation)
    errors = sorted(validator.iter_errors(payload), key=lambda e: e.absolute_path)
    if not errors:
        return None

    lines: list[str] = []
    for err in errors[:5]:  # cap so we don't blow the tool-result payload
        path = ".".join(str(p) for p in err.absolute_path) or "(root)"
        # Human-readable type explanation
        if err.validator == "type":
            expected = err.validator_value
            if isinstance(expected, list):
                expected = " или ".join(expected)
            got = type(err.instance).__name__
            got_human = {"str": "string", "int": "integer", "float": "number",
                         "bool": "boolean", "list": "array", "dict": "object",
                         "NoneType": "null"}.get(got, got)
            sample = repr(err.instance)[:60]
            lines.append(
                f"  • Параметр `{path}` ожидался {expected}, прислан {got_human} ({sample})."
            )
        else:
            lines.append(f"  • Параметр `{path}`: {err.message[:200]}")
    if len(errors) > 5:
        lines.append(f"  • … и ещё {len(errors) - 5} ошибок.")

    return (
        f"Аргументы tool `{tool_name}` не прошли валидацию по schema:\n"
        + "\n".join(lines)
        + "\n\nИсправь типы аргументов и вызови tool снова. Параметры с указанным "
        "типом должны передаваться в JSON в виде значений этого типа (integer = "
        "число без кавычек, boolean = true/false, array = [..], не строка)."
    )


def _get_at_path(obj, path: str):
    """Walk obj by dotted path. Returns (value, exists)."""
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None, False
    return cur, True


def _set_at_path(obj, path: str, value) -> bool:
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


def _apply_arg_formats(
    tool_name: str,
    arguments: dict,
    tool_config: dict | None,
) -> str | None:
    """Generic per-path format pipeline (tenant-agnostic).

    Tool config shape:
        x_backend_config.arg_formats = {
            "<dotted.path.to.leaf>": "<pipeline string>",
            ...
        }
    Executor walks each path, runs `normalize_or_validate`, and mutates
    arguments in place. Same engine the per-enum-value formats use; this
    just decouples it from the enum context so any leaf in any nested
    object can be normalized (filters.alias, body_params.foo, etc).
    """
    if not isinstance(tool_config, dict):
        return None
    runtime = tool_config.get("x_backend_config")
    if not isinstance(runtime, dict):
        return None
    arg_formats = runtime.get("arg_formats")
    if not isinstance(arg_formats, dict) or not arg_formats:
        return None

    from app.services.tools.format_template import normalize_or_validate

    for path, pipeline in arg_formats.items():
        if not isinstance(path, str) or not path.strip():
            continue
        if not isinstance(pipeline, str) or not pipeline.strip():
            continue
        cur, exists = _get_at_path(arguments, path)
        if not exists or cur in (None, ""):
            continue
        new_value, err = normalize_or_validate(cur, pipeline)
        if err:
            return f"Параметр `{path}` {err}."
        if new_value != cur and _set_at_path(arguments, path, new_value):
            logger.info(
                "[arg_formats] %s normalized %s: %r → %r",
                tool_name, path, cur, new_value,
            )
    return None


def _normalize_enum_formats(
    tool_name: str,
    arguments: dict,
    tool_config: dict | None,
) -> str | None:
    """Apply format templates to query_params in-place.

    Mechanism (tenant-agnostic, opt-in per tool):
      x_backend_config.enum_values[<path_name>] = [
        { "value": "<enum>", "requires": [...], "formats": { "<alias>": "<template>" } },
        ...
      ]
    Template grammar (see format_template.py): `x`/`X` = hex digit
    (lower/upper case in output), `9` = decimal, other chars literal.
    A `re:<regex>` prefix opts out of templating and validates raw.

    For each model-provided query_param: try to normalize it into the
    template's shape. If the input has the right number of data chars,
    it gets rewritten in place (e.g. `1C:EF:03:CA:79:A0` →
    `1cef.03ca.79a0`). If normalization is impossible AND it still
    doesn't match → return a tool error string for the model.
    """
    from app.services.tools.format_template import normalize_or_validate

    if not isinstance(tool_config, dict):
        return None
    runtime = tool_config.get("x_backend_config")
    if not isinstance(runtime, dict):
        return None
    enum_store = runtime.get("enum_values")
    if not isinstance(enum_store, dict):
        return None

    path_values = arguments.get("path_values") if isinstance(arguments.get("path_values"), dict) else {}
    query_params = arguments.get("query_params") if isinstance(arguments.get("query_params"), dict) else None
    if query_params is None:
        return None

    for path_name, entries in enum_store.items():
        if not isinstance(entries, list):
            continue
        selected = path_values.get(path_name)
        if selected in (None, ""):
            continue
        entry = next((e for e in entries if isinstance(e, dict) and e.get("value") == selected), None)
        if not entry:
            continue
        formats = entry.get("formats")
        if not isinstance(formats, dict) or not formats:
            continue
        for alias, fmt in formats.items():
            if not isinstance(fmt, str) or not fmt.strip():
                continue
            original = query_params.get(alias)
            if original in (None, ""):
                continue
            new_value, err = normalize_or_validate(original, fmt)
            if err:
                return (
                    f"Параметр `query_params.{alias}` для {path_name}={selected!r} "
                    f"{err}. Передай значение, которое можно привести к формату."
                )
            if new_value != original:
                query_params[alias] = new_value
                logger.info(
                    "[enum_formats] %s normalized query_params.%s for %s=%r: %r → %r",
                    tool_name, alias, path_name, selected, original, new_value,
                )
    return None


def _transform_json_for_result(obj, drop_fields: set[str], limit_items: int | None):
    """Recursive transform: drop noise keys, truncate arrays.

    For arrays past `limit_items`: keep the first N items and append a
    sentinel `{"_truncated": "+M more items"}` so the model SEES the
    omission and won't pretend it has the full list.
    """
    if isinstance(obj, dict):
        cleaned = {}
        for k, v in obj.items():
            if k in drop_fields:
                continue
            cleaned[k] = _transform_json_for_result(v, drop_fields, limit_items)
        return cleaned
    if isinstance(obj, list):
        truncated = 0
        items = obj
        if limit_items and len(obj) > limit_items:
            truncated = len(obj) - limit_items
            items = obj[:limit_items]
        out = [_transform_json_for_result(it, drop_fields, limit_items) for it in items]
        if truncated > 0:
            out.append({"_truncated": f"+{truncated} more items omitted"})
        return out
    return obj


def _filter_result_to_fields(obj, allowed: set[str]):
    """Recursive: for any dict at any level, keep only keys in `allowed`.

    Top-level non-data keys (count, items, _truncated, column_descriptions,
    summary, etc) are ALWAYS kept — we filter only the per-item dicts, not
    the envelope. That keeps cardinality info and pagination metadata while
    trimming the row payload.
    """
    ENVELOPE_KEYS = {
        "count", "items", "rows", "results", "data",
        "truncated", "shown_limit", "column_descriptions",
        "summary", "_truncated", "_summary", "severity",
        "log_truncated", "log_shown_rows", "page", "total_pages",
    }
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in ENVELOPE_KEYS:
                out[k] = _filter_result_to_fields(v, allowed)
            elif k in allowed:
                out[k] = v  # leaf data field — keep as-is, no recurse
        return out
    if isinstance(obj, list):
        return [_filter_result_to_fields(item, allowed) for item in obj]
    return obj


def _apply_selectable_fields(
    tool_name: str,
    arguments: dict,
    result: ToolResult,
    tool_config: dict | None,
) -> ToolResult:
    """Trim tool output to only the fields LLM asked for in `arguments.fields`.

    Activated when both conditions hold:
      • tool config declares `x_backend_config.selectable_fields: [...]`
      • LLM passed a non-empty `fields` array in this call's arguments

    If LLM passed `fields` but tool doesn't declare selectable_fields, we
    ignore it (no enforcement) to keep changes additive and safe.
    """
    if not isinstance(arguments, dict):
        return result
    fields_arg = arguments.get("fields")
    if not (isinstance(fields_arg, list) and fields_arg):
        return result

    if not isinstance(tool_config, dict):
        return result
    runtime = tool_config.get("x_backend_config")
    if not isinstance(runtime, dict):
        return result
    selectable = runtime.get("selectable_fields")
    if not (isinstance(selectable, list) and selectable):
        return result

    selectable_set = {str(s).strip() for s in selectable if isinstance(s, str)}
    asked = {str(f).strip() for f in fields_arg if isinstance(f, str)}
    allowed = asked & selectable_set  # silently ignore unknown field names
    if not allowed:
        return result

    output = result.output or ""
    if not output:
        return result
    try:
        parsed = json.loads(output)
    except (json.JSONDecodeError, ValueError):
        return result  # non-JSON output — can't filter

    filtered = _filter_result_to_fields(parsed, allowed)
    new_output = json.dumps(filtered, ensure_ascii=False)
    if new_output != output:
        logger.info(
            "[selectable_fields] %s: %d → %d chars (kept %s)",
            tool_name, len(output), len(new_output), sorted(allowed),
        )
        return ToolResult(success=result.success, output=new_output, error=result.error)
    return result


def _apply_result_processing(
    tool_name: str,
    result: ToolResult,
    tool_config: dict | None,
) -> ToolResult:
    """Generic post-processor for tool output.

    Reads `x_backend_config.result_processing` from the tool config and
    applies size-control transforms BEFORE the result reaches the LLM:
      - `drop_fields`: list of JSON keys to remove recursively (noise like
        `lat`, `lng`, `packet_id`, `updated_at`)
      - `limit_items`: cap any array length, append `_truncated` sentinel
      - `max_chars`: hard char limit on final output string (last resort)

    Same engine concept as `arg_formats` but on the OUTPUT side. Works for
    ANY tool — no per-tool code needed, just config.
    """
    if not isinstance(tool_config, dict):
        return result
    runtime = tool_config.get("x_backend_config")
    if not isinstance(runtime, dict):
        return result
    rp = runtime.get("result_processing")
    if not isinstance(rp, dict) or not rp:
        return result

    output = result.output or ""
    original_len = len(output)
    if original_len == 0:
        return result

    drop_fields = set(rp.get("drop_fields") or [])
    limit_items = rp.get("limit_items")
    if not (isinstance(limit_items, int) and limit_items > 0):
        limit_items = None

    if drop_fields or limit_items:
        try:
            parsed = json.loads(output)
            transformed = _transform_json_for_result(parsed, drop_fields, limit_items)
            output = json.dumps(transformed, ensure_ascii=False)
        except (json.JSONDecodeError, ValueError):
            pass

    max_chars = rp.get("max_chars")
    if isinstance(max_chars, int) and max_chars > 0 and len(output) > max_chars:
        cut = output[:max_chars]
        omitted = len(output) - max_chars
        output = f"{cut}\n\n[output truncated: {omitted} chars omitted, original={original_len}]"

    if output != (result.output or "") or original_len != len(output):
        logger.info(
            "[result_processing] %s: %d → %d chars (drop=%s limit=%s max_chars=%s)",
            tool_name, original_len, len(output),
            sorted(drop_fields) if drop_fields else None,
            limit_items, max_chars,
        )
        return ToolResult(success=result.success, output=output, error=result.error)
    return result


async def execute_tool(tool_name: str, arguments: dict, tool_config: dict | None = None) -> ToolResult:
    """Execute a registered tool by name with given arguments."""
    handler_name = resolve_tool_handler(tool_name, tool_config)
    handler = _HANDLERS.get(handler_name)
    if not handler:
        return ToolResult(
            success=False,
            output="",
            error=f"Инструмент '{handler_name}' не зарегистрирован. Доступные: {', '.join(_HANDLERS.keys())}",
        )

    # Argument validation against the tool's declared JSON schema. Catches
    # the "string '1-18' instead of integer" class of bugs before we waste a
    # round-trip to the backend API and come back with HTTP 404. The model
    # gets a precise error message and self-corrects on the next round.
    validation_error = _validate_arguments_against_schema(tool_name, arguments, tool_config)
    if validation_error:
        logger.info("[tool-validation] %s rejected: %s", tool_name, validation_error[:200])
        return ToolResult(success=False, output="", error=validation_error)

    # Generic per-path arg-format pipeline (tenant defines map from dotted
    # path to a format pipeline string). Runs first so context-free
    # normalization (e.g. "query_params.mac always lowercase-dotted") fires
    # before any enum-conditional rules below.
    arg_format_error = _apply_arg_formats(tool_name, arguments, tool_config)
    if arg_format_error:
        logger.info("[arg_formats] %s rejected: %s", tool_name, arg_format_error[:200])
        return ToolResult(success=False, output="", error=arg_format_error)

    # Per-enum-value format templates (e.g. "this command_key wants MAC as
    # xxxx.xxxx.xxxx"). Same engine as arg_formats above, but the format
    # depends on which enum value the model picked (different command_keys
    # may want different MAC layouts on the same alias).
    format_error = _normalize_enum_formats(tool_name, arguments, tool_config)
    if format_error:
        logger.info("[enum_formats] %s rejected: %s", tool_name, format_error[:200])
        return ToolResult(success=False, output="", error=format_error)

    # Determine timeout: respect configured timeout_seconds in tool_config (capped),
    # with sensible defaults for batch ping.
    timeout = TOOL_TIMEOUT_SECONDS
    runtime_cfg = _extract_runtime_config(tool_config) if tool_config else {}
    configured = runtime_cfg.get("timeout_seconds")
    if isinstance(configured, (int, float)) and configured > 0:
        timeout = min(float(configured), MAX_TOOL_TIMEOUT_SECONDS)
    if handler_name == "ping" and ("ips" in arguments or isinstance(arguments.get("ip"), list)):
        timeout = max(timeout, PING_BATCH_TIMEOUT_SECONDS)

    try:
        result = await asyncio.wait_for(handler(arguments, tool_config), timeout=timeout)
        # LLM-requested field projection runs first (smaller payload for the
        # subsequent generic transforms to chew on).
        result = _apply_selectable_fields(tool_name, arguments, result, tool_config)
        return _apply_result_processing(tool_name, result, tool_config)
    except asyncio.TimeoutError:
        # Log the actual tool name + args so we can see WHICH call timed out
        # (used to be invisible — the model just got a generic "Таймаут").
        logger.warning(
            "[tool-timeout] handler=%s timeout=%ss args=%r",
            handler_name, timeout, {k: v for k, v in arguments.items() if k != "_context"},
        )
        return ToolResult(success=False, output="", error=f"Таймаут выполнения {handler_name} ({timeout}с)")
    except Exception as e:
        logger.exception(f"Tool execution error: {handler_name}")
        return ToolResult(success=False, output="", error=f"Ошибка {handler_name}: {str(e)[:300]}")


# ============================================================
# Built-in tools
# ============================================================

def _validate_ip(ip: str) -> str:
    """Validate and sanitize IP address. Raises ValueError if invalid."""
    ip = ip.strip()
    # Allow hostname-like strings too (e.g. google.com) but sanitize
    if re.match(r'^[a-zA-Z0-9.\-:]+$', ip) and len(ip) <= 253:
        # Try to parse as IP first
        try:
            addr = ipaddress.ip_address(ip)
            # Block private/loopback for security
            if addr.is_loopback or addr.is_link_local:
                raise ValueError(f"Адрес {ip} запрещён (loopback/link-local)")
            return str(addr)
        except ValueError:
            # Not an IP, treat as hostname — basic validation
            if re.match(r'^[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?)*$', ip):
                return ip
    raise ValueError(f"Некорректный IP/хост: {ip}")


MAX_PING_BATCH = 50  # max IPs per single tool call
PING_CONCURRENCY = 20  # max parallel ping subprocesses
MAX_API_RESPONSE_CHARS = 16000
MAX_RECORD_LIMIT = 100


def _validate_identifier(name: str, *, dotted: bool = False) -> str:
    pattern = r"^[A-Za-z_][A-Za-z0-9_]*$"
    parts = name.split(".") if dotted else [name]
    if not parts or any(not re.match(pattern, part) for part in parts):
        raise ValueError(f"Некорректный идентификатор: {name}")
    return name


def _quote_identifier(name: str) -> str:
    _validate_identifier(name, dotted="." in name)
    return ".".join(f'"{part}"' for part in name.split("."))


def _database_kind(url: str) -> str:
    if url.startswith(("postgresql://", "postgresql+asyncpg://")):
        return "postgresql"
    if url.startswith(("mysql://", "mysql+asyncmy://", "mysql+aiomysql://", "mariadb://", "mariadb+aiomysql://")):
        return "mysql"
    raise ValueError("Поддерживаются только PostgreSQL и MariaDB/MySQL URL")


def _quote_identifier_for_db(name: str, db_kind: str) -> str:
    _validate_identifier(name, dotted="." in name)
    quote_char = '"' if db_kind == "postgresql" else "`"
    return ".".join(f"{quote_char}{part}{quote_char}" for part in name.split("."))


def _quote_table_with_alias(name: str, alias: str | None, db_kind: str) -> str:
    quoted = _quote_identifier_for_db(name, db_kind)
    if alias:
        _validate_identifier(alias)
        alias_quote = '"' if db_kind == "postgresql" else "`"
        return f"{quoted} {alias_quote}{alias}{alias_quote}"
    return quoted


def _text_expr(column_name: str, db_kind: str) -> str:
    # If the value contains '(' it's a raw SQL expression (e.g. CONCAT(...)) —
    # admin-configured, not user input, so safe to pass through without quoting.
    if "(" in column_name:
        return f"({column_name})"
    quoted = _quote_identifier_for_db(column_name, db_kind)
    if db_kind == "postgresql":
        return f"CAST({quoted} AS TEXT)"
    return f"CAST({quoted} AS CHAR)"


def _contains_expr(column_name: str, param_name: str, db_kind: str) -> str:
    text_expr = _text_expr(column_name, db_kind)
    if db_kind == "postgresql":
        return f"{text_expr} ILIKE :{param_name}"
    return f"LOWER({text_expr}) LIKE LOWER(:{param_name})"


def _starts_with_expr(column_name: str, param_name: str, db_kind: str) -> str:
    text_expr = _text_expr(column_name, db_kind)
    if db_kind == "postgresql":
        return f"{text_expr} ILIKE :{param_name}"
    return f"LOWER({text_expr}) LIKE LOWER(:{param_name})"


def _normalize_database_url(url: str) -> str:
    if url.startswith("postgresql+asyncpg://"):
        return url
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url.removeprefix("postgresql://")
    if url.startswith("mysql+asyncmy://"):
        return url
    if url.startswith("mysql://"):
        return "mysql+asyncmy://" + url.removeprefix("mysql://")
    if url.startswith("mariadb://"):
        return "mysql+asyncmy://" + url.removeprefix("mariadb://")
    raise ValueError("Поддерживаются только PostgreSQL и MariaDB/MySQL URL")


def _get_db_engine(url: str) -> AsyncEngine:
    normalized = _normalize_database_url(url)
    engine = _DB_ENGINES.get(normalized)
    if engine is None:
        engine = create_async_engine(
            normalized,
            pool_pre_ping=True,
            pool_recycle=900,  # recycle connections every 15 min to dodge idle timeouts
            pool_size=5,
            max_overflow=5,
        )
        # SQLAlchemy 2.0's asyncmy adapter exposes ping(self, reconnect) with NO
        # default, but inherits PyMySQL's do_ping which — because the installed
        # PyMySQL's Connection.ping defaults reconnect=False — calls ping() with
        # no args, raising "missing positional argument 'reconnect'" on every
        # pre-ping. Forcing _send_false_to_ping=True makes do_ping pass ping(False)
        # explicitly, which the adapter (and the real asyncmy connection) accept.
        if normalized.startswith("mysql+asyncmy://"):
            try:
                engine.sync_engine.dialect._send_false_to_ping = True
            except Exception:  # pragma: no cover — never let this break engine setup
                logger.warning("could not force _send_false_to_ping on asyncmy engine", exc_info=True)
        _DB_ENGINES[normalized] = engine
    return engine


_DEAD_CONN_MARKERS = (
    "the handler is closed",
    "TCPTransport closed",
    "connection is closed",
    "connection was closed",
    "connection has been closed",
    "ServerDisconnectedError",
    "CONNECTION_DOES_NOT_EXIST",
)


def _is_dead_connection_error(exc: BaseException) -> bool:
    msg = str(exc)
    return any(marker.lower() in msg.lower() for marker in _DEAD_CONN_MARKERS)


def _extract_runtime_config(tool_config: dict | None) -> dict:
    if not isinstance(tool_config, dict):
        return {}
    for key in ("x_backend_config", "backend_config", "runtime_config"):
        value = tool_config.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _is_scalar(value) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _normalize_scalar(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if value is None:
        return None
    return str(value).strip()


def _get_database_url(source: str) -> str:
    if source == "main":
        return settings.DATABASE_URL
    raise ValueError(f"Неизвестный источник БД: {source}")


async def _load_tenant_data_source(data_source_id: str) -> dict:
    from sqlalchemy import select

    from app.core.database import async_session
    from app.models.tenant_data_source import TenantDataSource

    async with async_session() as db:
        ds = (
            await db.execute(
                select(TenantDataSource).where(
                    TenantDataSource.id == data_source_id,
                    TenantDataSource.deleted_at.is_(None),
                    TenantDataSource.is_active == True,  # noqa: E712
                )
            )
        ).scalars().first()
        if not ds:
            raise ValueError(f"Источник данных не найден или неактивен: {data_source_id}")
        secret_json = {}
        if ds.secret_json_encrypted:
            try:
                secret_json = json.loads(decrypt_value(ds.secret_json_encrypted))
            except Exception as e:
                raise ValueError(f"Не удалось расшифровать секрет источника данных: {str(e)}") from e
        return {
            "id": str(ds.id),
            "kind": ds.kind,
            "config_json": ds.config_json or {},
            "secret_json": secret_json,
            "name": ds.name,
        }


def _build_db_url_from_data_source(data_source: dict) -> str:
    kind = str(data_source.get("kind") or "").strip().lower()
    config = data_source.get("config_json") if isinstance(data_source.get("config_json"), dict) else {}
    secret = data_source.get("secret_json") if isinstance(data_source.get("secret_json"), dict) else {}

    host = str(config.get("host") or "").strip()
    port = config.get("port")
    database = str(config.get("database") or "").strip()
    username = str(config.get("username") or "").strip()
    password = secret.get("password")
    if not host or not database or not username or password is None:
        raise ValueError("У источника данных БД должны быть host, database, username и password")

    if kind == "postgresql":
        port_part = f":{int(port)}" if port else ""
        return f"postgresql+asyncpg://{username}:{password}@{host}{port_part}/{database}"
    if kind in {"mariadb", "mysql"}:
        port_part = f":{int(port)}" if port else ""
        return f"mysql+asyncmy://{username}:{password}@{host}{port_part}/{database}"
    raise ValueError(f"Источник данных '{kind}' не является БД")


def _build_match_condition(
    *,
    db_kind: str,
    column_name: str,
    mode: str,
    param_name: str,
    value,
) -> tuple[str, object]:
    normalized = _normalize_scalar(value)
    if normalized is None or normalized == "":
        raise ValueError(f"Пустое значение фильтра для {column_name}")

    if mode == "exact":
        return f"{_text_expr(column_name, db_kind)} = :{param_name}", str(normalized)
    if mode == "contains":
        return _contains_expr(column_name, param_name, db_kind), f"%{normalized}%"
    if mode == "starts_with":
        return _starts_with_expr(column_name, param_name, db_kind), f"{normalized}%"
    if mode == "gte":
        return f"{_quote_identifier_for_db(column_name, db_kind)} >= :{param_name}", normalized
    if mode == "lte":
        return f"{_quote_identifier_for_db(column_name, db_kind)} <= :{param_name}", normalized
    if mode == "eq":
        return f"{_quote_identifier_for_db(column_name, db_kind)} = :{param_name}", normalized
    raise ValueError(f"Неподдерживаемый режим фильтра: {mode}")


async def _fetch_sql_rows(db_url: str, sql: str, params: dict) -> list[dict]:
    engine = _get_db_engine(db_url)
    last_exc: BaseException | None = None
    for attempt in range(2):
        try:
            async with engine.connect() as conn:
                result = await conn.execute(text(sql), params)
                return [dict(row) for row in result.mappings().all()]
        except Exception as e:
            last_exc = e
            if not _is_dead_connection_error(e):
                raise
            logger.warning(
                "SQL tool: dead connection detected (attempt %d/2): %s — retrying with fresh pool",
                attempt + 1, type(e).__name__,
            )
            try:
                await engine.dispose()
            except Exception:  # noqa: BLE001
                pass
    assert last_exc is not None
    raise last_exc


def _build_records_output(
    rows: list[dict],
    limit: int | None,
    result_columns: list[str],
    column_descriptions: dict[str, str] | None = None,
) -> str:
    shown_rows = rows if limit is None else rows[:limit]
    normalized_items: list[dict[str, object | None]] = []
    for row in shown_rows:
        normalized_row: dict[str, object | None] = {}
        for col in result_columns:
            normalized_row[col] = row.get(col)
        normalized_items.append(normalized_row)

    payload: dict[str, object] = {
        "count": len(shown_rows),
        "items": normalized_items,
    }
    if limit is not None and len(rows) > limit:
        payload["truncated"] = True
        payload["shown_limit"] = limit
    if column_descriptions:
        meaningful = {col: desc for col, desc in column_descriptions.items() if desc}
        if meaningful:
            payload["column_descriptions"] = meaningful
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def _build_records_query(arguments: dict, runtime: dict, db_url: str) -> tuple[str, dict, list[str], int | None, dict[str, str]]:
    db_kind = _database_kind(db_url)
    table_name = str(runtime.get("table") or runtime.get("view") or "").strip()
    if not table_name:
        raise ValueError("В конфигурации инструмента не указано table/view")
    table_alias = str(runtime.get("table_alias") or "").strip() or None

    filter_fields = runtime.get("filter_fields")
    if not isinstance(filter_fields, dict) or not filter_fields:
        raise ValueError("В конфигурации инструмента не указан filter_fields")

    result_columns = runtime.get("result_columns")
    if not isinstance(result_columns, list) or not result_columns:
        raise ValueError("В конфигурации инструмента не указан result_columns")
    output_columns: list[str] = []
    select_column_exprs: list[str] = []
    column_descriptions: dict[str, str] = {}
    for item in result_columns:
        if isinstance(item, str):
            output_columns.append(item)
            select_column_exprs.append(_quote_identifier_for_db(item, db_kind))
        elif isinstance(item, dict):
            column_name = str(item.get("column") or "").strip()
            alias_name = str(item.get("alias") or "").strip()
            if not column_name or not alias_name:
                raise ValueError("Каждый result_columns object должен содержать column и alias")
            output_columns.append(alias_name)
            alias_quote = '"' if db_kind == "postgresql" else "`"
            # If column_name looks like an SQL expression (contains parens) pass it
            # through as-is — admin-configured, not user input.
            col_expr = f"({column_name})" if "(" in column_name else _quote_identifier_for_db(column_name, db_kind)
            select_column_exprs.append(
                f"{col_expr} AS {alias_quote}{alias_name}{alias_quote}"
            )
            desc = str(item.get("description") or "").strip()
            if desc:
                column_descriptions[alias_name] = desc
        else:
            raise ValueError("result_columns должен содержать строки или объекты {column, alias}")

    unlimited_results = bool(runtime.get("unlimited_results"))
    limit: int | None = None
    if not unlimited_results:
        default_limit = int(runtime.get("default_limit") or 10)
        max_limit = min(int(runtime.get("max_limit") or 25), MAX_RECORD_LIMIT)
        limit_arg = arguments.get("limit", default_limit)
        try:
            limit = max(1, min(int(limit_arg), max_limit))
        except (TypeError, ValueError):
            limit = default_limit

    conditions: list[str] = []
    params: dict[str, object] = {}

    filters = arguments.get("filters")
    if filters is None:
        filters = {}
    if not isinstance(filters, dict):
        raise ValueError("Параметр filters должен быть объектом")

    for alias, raw_value in filters.items():
        field_cfg = filter_fields.get(alias)
        if not isinstance(field_cfg, dict):
            raise ValueError(f"Фильтр '{alias}' не разрешён для этого инструмента")
        column_name = str(field_cfg.get("column") or "").strip()
        mode = str(field_cfg.get("mode") or "exact").strip().lower()
        if not column_name:
            raise ValueError(f"У фильтра '{alias}' не указана колонка")
        if raw_value is None or raw_value == "":
            continue
        param_name = f"filter_{alias}"
        condition, param_value = _build_match_condition(
            db_kind=db_kind,
            column_name=column_name,
            mode=mode,
            param_name=param_name,
            value=raw_value,
        )
        conditions.append(condition)
        params[param_name] = param_value

    free_query = arguments.get("query")
    search_columns = runtime.get("search_columns")
    if free_query:
        if not isinstance(search_columns, list) or not search_columns:
            allowed_filters = list((runtime.get("filter_fields") or {}).keys())
            hint = (
                f" Доступные filters: {', '.join(allowed_filters)}." if allowed_filters else ""
            )
            raise ValueError(
                "Для этого инструмента free-text query не настроен. "
                "Используй filters вместо query." + hint
            )
        # search_word_mode: replace spaces with % so "ул 14Б кв 5" → %ул%14Б%кв%5%
        # Each word must appear somewhere in the column — useful for CONCAT address fields.
        word_mode = bool(runtime.get("search_word_mode"))
        raw_q = str(free_query).strip()
        if word_mode:
            query_pattern = "%" + re.sub(r"\s+", "%", raw_q) + "%"
        else:
            query_pattern = f"%{raw_q}%"
        query_terms = []
        for idx, column in enumerate(search_columns):
            param_name = f"query_{idx}"
            query_terms.append(_contains_expr(str(column), param_name, db_kind))
            params[param_name] = query_pattern
        conditions.append("(" + " OR ".join(query_terms) + ")")

    static_filters = runtime.get("static_filters")
    if isinstance(static_filters, dict):
        for alias, raw_value in static_filters.items():
            if not _is_scalar(raw_value):
                continue
            column_name = str(alias).strip()
            if not column_name:
                continue
            param_name = f"static_{re.sub(r'[^a-zA-Z0-9_]', '_', column_name)}"
            conditions.append(f"{_quote_identifier_for_db(column_name, db_kind)} = :{param_name}")
            params[param_name] = raw_value

    date_window = runtime.get("date_window")
    if isinstance(date_window, dict):
        column_name = str(date_window.get("column") or "").strip()
        days_raw = date_window.get("days")
        if not column_name or days_raw in (None, ""):
            raise ValueError("date_window должен содержать column и days")
        try:
            days = max(1, int(days_raw))
        except (TypeError, ValueError) as e:
            raise ValueError("date_window.days должен быть целым числом") from e
        param_name = f"date_window_{re.sub(r'[^a-zA-Z0-9_]', '_', column_name)}"
        if db_kind == "postgresql":
            conditions.append(
                f"{_quote_identifier_for_db(column_name, db_kind)} >= CURRENT_TIMESTAMP - (:"
                f"{param_name} * INTERVAL '1 day')"
            )
            params[param_name] = days
        else:
            conditions.append(
                f"{_quote_identifier_for_db(column_name, db_kind)} >= DATE_SUB(NOW(), INTERVAL :{param_name} DAY)"
            )
            params[param_name] = days

    if not conditions:
        allowed = ", ".join(sorted(str(key) for key in filter_fields.keys()))
        raise ValueError(f"Нужно передать filters или query. Разрешённые filters: {allowed}")

    quoted_table = _quote_table_with_alias(table_name, table_alias, db_kind)
    joins = runtime.get("joins")
    join_clauses: list[str] = []
    if joins is not None:
        if not isinstance(joins, list):
            raise ValueError("joins должен быть массивом")
        for idx, join_cfg in enumerate(joins):
            if not isinstance(join_cfg, dict):
                raise ValueError("Элементы joins должны быть объектами")
            join_type = str(join_cfg.get("type") or "left").strip().lower()
            if join_type not in {"left", "inner"}:
                raise ValueError(f"Неподдерживаемый тип join: {join_type}")
            join_table = str(join_cfg.get("table") or "").strip()
            if not join_table:
                raise ValueError(f"У joins[{idx}] не указана table")
            join_alias = str(join_cfg.get("alias") or "").strip() or None
            left_column = str(join_cfg.get("left_column") or "").strip()
            right_column = str(join_cfg.get("right_column") or "").strip()
            if not left_column or not right_column:
                raise ValueError(f"У joins[{idx}] должны быть left_column и right_column")
            join_keyword = "LEFT JOIN" if join_type == "left" else "INNER JOIN"
            join_clauses.append(
                f"{join_keyword} {_quote_table_with_alias(join_table, join_alias, db_kind)} "
                f"ON {_quote_identifier_for_db(left_column, db_kind)} = {_quote_identifier_for_db(right_column, db_kind)}"
            )

    select_cols = ", ".join(select_column_exprs)
    sort_by = str(runtime.get("sort_by") or output_columns[0]).strip()
    sql = (
        f"SELECT {select_cols} "
        f"FROM {quoted_table} "
        f"{' '.join(join_clauses)} "
        f"WHERE {' AND '.join(conditions)} "
        f"ORDER BY {_quote_identifier_for_db(sort_by, db_kind)} "
    )
    if limit is not None:
        sql += "LIMIT :limit_plus_one"
        params["limit_plus_one"] = limit + 1
    return sql, params, output_columns, limit, column_descriptions


async def _resolve_database_url(runtime: dict) -> str:
    data_source_id = runtime.get("data_source_id")
    if data_source_id:
        data_source = await _load_tenant_data_source(str(data_source_id))
        return _build_db_url_from_data_source(data_source)
    source = str(runtime.get("source") or "main").strip().lower()
    return _get_database_url(source)


def _extract_json_path_value(data, path: str):
    current = data
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            idx = int(part)
            current = current[idx] if 0 <= idx < len(current) else None
        else:
            return None
    return current


def _render_api_payload(
    payload,
    result_path: str | None = None,
    max_chars: int | None = None,
) -> str:
    target = payload
    if result_path:
        extracted = _extract_json_path_value(payload, result_path)
        if extracted is not None:
            target = extracted
    rendered = str(target) if isinstance(target, str) else json.dumps(target, ensure_ascii=False, indent=2)
    limit = max_chars if (isinstance(max_chars, int) and max_chars > 0) else MAX_API_RESPONSE_CHARS
    if len(rendered) > limit:
        return rendered[:limit] + "\n... [truncated]"
    return rendered


def _build_api_request(arguments: dict, runtime: dict, data_source: dict | None = None) -> tuple[str, str, dict, dict, dict | None, float, str | None]:
    data_source_config = data_source.get("config_json") if data_source and isinstance(data_source.get("config_json"), dict) else {}
    data_source_secret = data_source.get("secret_json") if data_source and isinstance(data_source.get("secret_json"), dict) else {}

    base_url = str(runtime.get("base_url") or data_source_config.get("base_url") or "").strip().rstrip("/")
    endpoint = str(runtime.get("endpoint") or "").strip()
    if not base_url or not endpoint:
        raise ValueError("В конфигурации инструмента должны быть указаны base_url и endpoint")
    if not base_url.startswith(("http://", "https://")):
        raise ValueError("base_url должен начинаться с http:// или https://")

    method = str(runtime.get("method") or "GET").strip().upper()
    if method not in {"GET", "POST"}:
        raise ValueError(f"Поддерживаются только GET и POST, получен {method}")

    allowed_path_params = runtime.get("path_params")
    allowed_query_params = runtime.get("query_params")
    if allowed_path_params is None:
        allowed_path_params = []
    if allowed_query_params is None:
        allowed_query_params = {}

    if not isinstance(allowed_path_params, list):
        raise ValueError("path_params должен быть массивом")
    if not isinstance(allowed_query_params, dict):
        raise ValueError("query_params должен быть объектом")

    path_values = dict(arguments.get("path_values") or {})
    query_values = dict(arguments.get("query_params") or {})
    if not isinstance(path_values, dict):
        raise ValueError("Параметр path_values должен быть объектом")
    if not isinstance(query_values, dict):
        raise ValueError("Параметр query_params должен быть объектом")

    # Forgive common LLM mistakes:
    # 1) Parameter in the wrong bucket (path↔query) — move it.
    # 2) Parameter at the root level (no bucket at all) — treat as flat.
    # 3) Common aliases: address|addr|query → q, etc.
    allowed_path_set = {str(k) for k in allowed_path_params}
    allowed_query_set = {str(k) for k in allowed_query_params}
    _COMMON_ALIASES = {
        # The LLM tends to use natural English/Russian names; map them onto the schema key.
        "address": "q", "addr": "q", "query": "q", "search": "q", "text": "q",
        "адрес": "q", "запрос": "q",
        "client_id": "id", "customer_id": "id",
        "switch": "switch_id", "device_id": "switch_id", "dev_id": "switch_id",
        "mac_address": "mac",
        "ip_address": "ip",
        "latitude": "lat", "longitude": "lon", "lng": "lon", "long": "lon",
    }

    def _retarget(key: str) -> str:
        return _COMMON_ALIASES.get(key, key)

    # Flat-style: arguments has neither bucket. Spread root keys into the right bucket.
    if not arguments.get("path_values") and not arguments.get("query_params"):
        for k, v in list(arguments.items()):
            if k in ("path_values", "query_params"):
                continue
            target = _retarget(k)
            if target in allowed_path_set:
                path_values.setdefault(target, v)
            elif target in allowed_query_set:
                query_values.setdefault(target, v)
        # Last-resort: model gave one scalar arg and there's exactly one required query key
        if not path_values and not query_values:
            required_q = [k for k in allowed_query_set]
            scalars = [(k, v) for k, v in arguments.items()
                       if k not in ("path_values", "query_params")
                       and isinstance(v, (str, int, float))]
            if len(required_q) == 1 and len(scalars) == 1:
                query_values[required_q[0]] = scalars[0][1]

    # In-bucket misplacements + aliases
    for key in list(path_values.keys()):
        target = _retarget(key)
        if target in allowed_path_set:
            if target != key:
                path_values[target] = path_values.pop(key)
            continue
        if target in allowed_query_set and target not in query_values:
            query_values[target] = path_values.pop(key)
    for key in list(query_values.keys()):
        target = _retarget(key)
        if target in allowed_query_set:
            if target != key:
                query_values[target] = query_values.pop(key)
            continue
        if target in allowed_path_set and target not in path_values:
            path_values[target] = query_values.pop(key)

    formatted_endpoint = endpoint
    for key in allowed_path_params:
        key_str = str(key)
        if "{" + key_str + "}" not in formatted_endpoint:
            continue
        if key_str not in path_values:
            raise ValueError(f"Не передан path_values.{key_str}")
        raw_value = _normalize_scalar(path_values[key_str])
        if raw_value is None or raw_value == "":
            raise ValueError(f"Пустое значение path_values.{key_str}")
        formatted_endpoint = formatted_endpoint.replace("{" + key_str + "}", str(raw_value))

    query_params_cfg: dict[str, str] = {}
    for public_name, target_name in allowed_query_params.items():
        query_params_cfg[str(public_name)] = str(target_name)

    final_query: dict[str, str | int | float | bool] = {}
    for public_name, raw_value in query_values.items():
        target_name = query_params_cfg.get(str(public_name))
        if not target_name:
            raise ValueError(f"Query-параметр '{public_name}' не разрешён")
        normalized = _normalize_scalar(raw_value)
        if normalized is None or normalized == "":
            continue
        final_query[target_name] = normalized

    static_query = runtime.get("static_query")
    if isinstance(static_query, dict):
        for key, raw_value in static_query.items():
            if _is_scalar(raw_value):
                final_query[str(key)] = raw_value

    headers = runtime.get("headers")
    if headers is None:
        headers = {}
    if not isinstance(headers, dict):
        raise ValueError("headers должен быть объектом")

    auth_type = str(data_source_config.get("auth_type") or "none").strip().lower()
    if auth_type == "bearer" and data_source_secret.get("token"):
        headers["Authorization"] = f"Bearer {data_source_secret['token']}"
    elif auth_type == "header" and data_source_secret.get("token"):
        header_name = str(data_source_config.get("auth_header_name") or "X-API-Key").strip()
        headers[header_name] = str(data_source_secret["token"])
    elif auth_type == "basic" and data_source_config.get("username") and data_source_secret.get("password"):
        import base64
        creds = f"{data_source_config['username']}:{data_source_secret['password']}"
        headers["Authorization"] = "Basic " + base64.b64encode(creds.encode()).decode()

    # Build body for POST (whitelisted via body_params + static_body)
    final_body: dict | None = None
    if method == "POST":
        allowed_body_params = runtime.get("body_params") or {}
        if not isinstance(allowed_body_params, dict):
            raise ValueError("body_params должен быть объектом")
        body_values = arguments.get("body_params") or {}
        if not isinstance(body_values, dict):
            raise ValueError("Параметр body_params должен быть объектом")

        body_map: dict[str, str] = {str(k): str(v) for k, v in allowed_body_params.items()}
        final_body = {}
        for public_name, raw_value in body_values.items():
            target_name = body_map.get(str(public_name))
            if not target_name:
                raise ValueError(f"Body-параметр '{public_name}' не разрешён")
            normalized = _normalize_scalar(raw_value)
            if normalized is None:
                continue
            final_body[target_name] = normalized

        static_body = runtime.get("static_body")
        if isinstance(static_body, dict):
            for key, raw_value in static_body.items():
                # do not override values explicitly passed by the model
                if str(key) not in final_body:
                    final_body[str(key)] = raw_value

        body_format = str(runtime.get("body_format") or "json").strip().lower()
        if body_format == "json" and "Content-Type" not in headers:
            headers["Content-Type"] = "application/json"

    timeout_seconds = float(runtime.get("timeout_seconds") or 15)
    result_path = str(runtime.get("result_path")).strip() if runtime.get("result_path") else None
    query_suffix = f"?{urlencode(final_query, doseq=False)}" if final_query else ""
    return method, f"{base_url}{formatted_endpoint}{query_suffix}", {str(k): str(v) for k, v in headers.items()}, final_query, final_body, timeout_seconds, result_path


async def _ping_one(target: str) -> str:
    """Ping a single validated target. Returns a one-line result string."""
    cmd = ["ping", "-c", "2", "-W", "3", target]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=TOOL_TIMEOUT_SECONDS)
        output = stdout.decode("utf-8", errors="replace")

        if proc.returncode == 0:
            # Extract rtt line: "rtt min/avg/max/mdev = 0.5/1.2/2.0/0.3 ms"
            for line in output.strip().split("\n"):
                if "min/avg/max" in line:
                    return f"{target}: OK — {line.strip()}"
            return f"{target}: OK"
        else:
            return f"{target}: UNREACHABLE"
    except asyncio.TimeoutError:
        return f"{target}: TIMEOUT"


@register_tool("ping")
async def tool_ping(arguments: dict, tool_config: dict | None = None) -> ToolResult:
    """Ping one or multiple IP addresses/hostnames in parallel.

    Accepts:
      - ip: str  — single address (backward compatible)
      - ips: list[str] — array of addresses for batch parallel ping
    """
    # Support both single "ip" and batch "ips"
    ips_raw: list[str] = []
    if "ips" in arguments and isinstance(arguments["ips"], list):
        ips_raw = arguments["ips"]
    elif "ip" in arguments:
        val = arguments["ip"]
        if isinstance(val, list):
            ips_raw = val
        elif isinstance(val, str):
            ips_raw = [val]

    if not ips_raw:
        return ToolResult(success=False, output="", error="Параметр 'ip' или 'ips' обязателен")

    if len(ips_raw) > MAX_PING_BATCH:
        return ToolResult(
            success=False, output="",
            error=f"Максимум {MAX_PING_BATCH} адресов за один вызов, передано {len(ips_raw)}",
        )

    # Validate all targets first
    targets: list[str] = []
    errors: list[str] = []
    for raw in ips_raw:
        try:
            targets.append(_validate_ip(str(raw)))
        except ValueError as e:
            errors.append(str(e))

    if not targets:
        return ToolResult(success=False, output="", error="; ".join(errors))

    # Ping all targets concurrently with semaphore
    sem = asyncio.Semaphore(PING_CONCURRENCY)

    async def _limited_ping(t: str) -> str:
        async with sem:
            return await _ping_one(t)

    results = await asyncio.gather(*[_limited_ping(t) for t in targets])

    output_lines = list(results)
    if errors:
        output_lines.append(f"\nValidation errors: {'; '.join(errors)}")

    return ToolResult(success=True, output="\n".join(output_lines))


@register_tool("dns_lookup")
async def tool_dns_lookup(arguments: dict, tool_config: dict | None = None) -> ToolResult:
    """Resolve a hostname to IP addresses."""
    host = arguments.get("host", "") or arguments.get("domain", "")
    if not host:
        return ToolResult(success=False, output="", error="Параметр 'host' обязателен")

    host = host.strip()
    if not re.match(r'^[a-zA-Z0-9.\-]+$', host) or len(host) > 253:
        return ToolResult(success=False, output="", error=f"Некорректный хост: {host}")

    try:
        import socket
        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(None, lambda: socket.getaddrinfo(host, None))
        ips = sorted(set(r[4][0] for r in results))
        return ToolResult(success=True, output=f"DNS {host}: {', '.join(ips)}")
    except socket.gaierror:
        return ToolResult(success=False, output="", error=f"Не удалось разрешить {host}")


@register_tool("traceroute")
async def tool_traceroute(arguments: dict, tool_config: dict | None = None) -> ToolResult:
    """Traceroute to a host."""
    ip_raw = arguments.get("ip", "") or arguments.get("host", "")
    if not ip_raw:
        return ToolResult(success=False, output="", error="Параметр 'ip' обязателен")

    try:
        target = _validate_ip(ip_raw)
    except ValueError as e:
        return ToolResult(success=False, output="", error=str(e))

    cmd = ["traceroute", "-m", "15", "-w", "3", target]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        output = stdout.decode("utf-8", errors="replace")
        return ToolResult(success=True, output=output[:2000])
    except asyncio.TimeoutError:
        return ToolResult(success=False, output="", error="Traceroute: таймаут (30с)")
    except FileNotFoundError:
        return ToolResult(success=False, output="", error="traceroute не установлен на сервере")


@register_tool("search_records")
async def tool_search_records(arguments: dict, tool_config: dict | None = None) -> ToolResult:
    """
    Generic read-only SELECT tool with whitelist-based filters.

    SQL is built server-side from runtime config. The model never sends raw SQL.
    """
    runtime = _extract_runtime_config(tool_config)

    try:
        db_url = await _resolve_database_url(runtime)
        sql, params, result_columns, limit, column_descriptions = _build_records_query(arguments, runtime, db_url)
        rows = await _fetch_sql_rows(db_url, sql, params)
        return ToolResult(success=True, output=_build_records_output(rows, limit, result_columns, column_descriptions))
    except ValueError as e:
        return ToolResult(success=False, output="", error=str(e))


async def _fetch_api_single(arguments: dict, runtime: dict) -> tuple[bool, str, str]:
    """Single HTTP call — shared by tool_fetch_api_data and its batch path.
    Returns (success, output, error)."""
    try:
        data_source = None
        if runtime.get("data_source_id"):
            data_source = await _load_tenant_data_source(str(runtime.get("data_source_id")))
            if str(data_source.get("kind") or "").strip().lower() != "http_api":
                return (False, "", "Указанный источник данных не является HTTP API")
        method, url, headers, _query_params, body, timeout_seconds, result_path = _build_api_request(arguments, runtime, data_source)
    except ValueError as e:
        return (False, "", str(e))

    body_format = str(runtime.get("body_format") or "json").strip().lower()
    request_kwargs: dict = {"headers": headers}
    if method == "POST" and body is not None:
        if body_format == "form":
            request_kwargs["data"] = body
        else:
            request_kwargs["json"] = body

    try:
        response = await _get_http_client().request(method, url, timeout=timeout_seconds, **request_kwargs)
        response.raise_for_status()
    except httpx.HTTPStatusError as e:
        return (False, "", f"HTTP {e.response.status_code}: {(e.response.text or '')[:500]}")
    except httpx.HTTPError as e:
        return (False, "", f"Ошибка HTTP-клиента: {str(e)}")

    runtime_max_chars = runtime.get("max_response_chars")
    if not (isinstance(runtime_max_chars, int) and runtime_max_chars > 0):
        runtime_max_chars = None
    effective_limit = runtime_max_chars or MAX_API_RESPONSE_CHARS

    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            payload = response.json()
        except ValueError:
            return (False, "", "API вернул некорректный JSON")
        return (True, _render_api_payload(payload, result_path, effective_limit), "")

    text_body = response.text[:effective_limit]
    if len(response.text) > effective_limit:
        text_body += "\n... [truncated]"
    return (True, text_body, "")


def _expand_path_batch(arguments: dict, runtime: dict) -> list[dict]:
    """If any of the path-params listed in `batchable_path_params` carries a
    batch syntax — range like '1-18', csv like '1,3,5', or an actual list —
    return a list of argument-dicts, one per expanded value. Otherwise return
    a single-element list (no expansion).

    Cap at 64 expansions to avoid blowing up backend with accidental ranges.
    """
    batchable = runtime.get("batchable_path_params") or []
    if not isinstance(batchable, list) or not batchable:
        return [arguments]
    pv = (arguments.get("path_values") or {})
    if not isinstance(pv, dict):
        return [arguments]

    BATCH_CAP = 64

    def _expand(raw):
        # Already a list?
        if isinstance(raw, list):
            return [str(x) for x in raw if x is not None]
        s = str(raw).strip()
        # Range "A-B" with both ends numeric → [A..B]
        m = re.fullmatch(r"\s*(\d+)\s*-\s*(\d+)\s*", s)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a > b:
                a, b = b, a
            return [str(i) for i in range(a, b + 1)]
        # CSV with at least one comma and all numeric tokens
        if "," in s:
            tokens = [t.strip() for t in s.split(",") if t.strip()]
            if tokens and all(t.isdigit() for t in tokens):
                return tokens
        return None  # no batch syntax — leave as scalar

    expanded_values: dict[str, list[str]] = {}
    for key in batchable:
        if key not in pv:
            continue
        vals = _expand(pv[key])
        if vals is None:
            continue
        if len(vals) == 1:
            continue
        expanded_values[key] = vals

    if not expanded_values:
        return [arguments]

    # If multiple batchable params got expanded we just do a cross product over
    # them. In practice it'll usually be one (e.g. port_index).
    keys = list(expanded_values.keys())
    out: list[dict] = []
    def _walk(i: int, cur: dict):
        if i == len(keys):
            new_pv = dict(pv)
            new_pv.update(cur)
            out.append({**arguments, "path_values": new_pv})
            return
        k = keys[i]
        for v in expanded_values[k]:
            _walk(i + 1, {**cur, k: v})
    _walk(0, {})
    return out[:BATCH_CAP]


@register_tool("fetch_api_data")
async def tool_fetch_api_data(arguments: dict, tool_config: dict | None = None) -> ToolResult:
    """
    Generic read-only HTTP API fetch tool.

    The admin config defines base_url, endpoint template and whitelisted params.
    If the tool's `batchable_path_params` lists path-params (e.g. ["port_index"])
    and the model passes a range/csv/list for them, we expand and run all calls
    in parallel, returning a combined view. This is how the model can request
    "ports 1-18" in one call instead of 18.
    """
    runtime = _extract_runtime_config(tool_config)

    # Expand batch path-params if any.
    expanded = _expand_path_batch(arguments, runtime)
    if len(expanded) > 1:
        sem = asyncio.Semaphore(API_BATCH_CONCURRENCY)

        async def _limited_fetch(args: dict):
            async with sem:
                return await _fetch_api_single(args, runtime)

        results = await asyncio.gather(
            *[_limited_fetch(args) for args in expanded],
            return_exceptions=True,
        )
        batchable = runtime.get("batchable_path_params") or []
        label_key = batchable[0] if batchable else None

        # Normalize each result into (label, ok, output, err_signature, err_text)
        # err_signature is a short normalized form used to group identical errors.
        def _err_sig(err_text: str) -> str:
            # HTTP 404 / 500 style — keep the status code, drop the URL/body.
            m = re.match(r"^HTTP (\d+):", err_text or "")
            if m:
                return f"http_{m.group(1)}"
            # Strip numbers and quoted strings so e.g. "param 'foo' invalid" and
            # "param 'bar' invalid" collapse.
            sig = re.sub(r"\d+", "N", err_text or "")
            sig = re.sub(r"['\"][^'\"]+['\"]", "X", sig)
            return sig[:80]

        successes: list[tuple[str | None, str]] = []
        # err_groups: signature -> {"sample": full_err_text, "labels": [labels]}
        err_groups: dict[str, dict] = {}
        for args, r in zip(expanded, results):
            label = (args.get("path_values") or {}).get(label_key) if label_key else None
            if isinstance(r, Exception):
                sig = f"exc_{type(r).__name__}"
                err_groups.setdefault(sig, {"sample": f"{type(r).__name__}: {r}", "labels": []})
                err_groups[sig]["labels"].append(str(label))
                continue
            ok, output, err = r
            if ok:
                successes.append((label, output))
            else:
                sig = _err_sig(err)
                err_groups.setdefault(sig, {"sample": err, "labels": []})
                err_groups[sig]["labels"].append(str(label))

        lines = [
            f"Batch результат ({len(expanded)} вызовов{label_key and f' по {label_key}' or ''}): "
            f"{len(successes)} ok, {sum(len(g['labels']) for g in err_groups.values())} ошибок."
        ]
        # Print successes in their own section
        for label, output in successes:
            tag = f"{label_key}={label}" if label is not None else "(вызов)"
            lines.append(f"--- {tag}\n{output}")
        # Group errors by signature — say once "404 на портах 1..18" instead
        # of pasting the same Symfony stacktrace 18 times.
        for sig, info in err_groups.items():
            labels = info["labels"]
            sample = (info["sample"] or "")[:400]
            if len(labels) >= 3:
                lines.append(
                    f"--- ОШИБКА на {len(labels)} вызовах "
                    f"({label_key}={', '.join(labels[:8])}{'...' if len(labels) > 8 else ''}): {sample}\n"
                    f"    Все {len(labels)} вызовов вернули одну и ту же ошибку — "
                    f"параметры вероятно неверные, повторять не нужно."
                )
            else:
                for label in labels:
                    tag = f"{label_key}={label}" if label is not None else "(вызов)"
                    lines.append(f"--- {tag} → ошибка: {sample}")
        # Tool result is "successful" iff at least one sub-call succeeded.
        return ToolResult(success=bool(successes), output="\n".join(lines))

    # Single-call path (unchanged).
    try:
        data_source = None
        if runtime.get("data_source_id"):
            data_source = await _load_tenant_data_source(str(runtime.get("data_source_id")))
            if str(data_source.get("kind") or "").strip().lower() != "http_api":
                raise ValueError("Указанный источник данных не является HTTP API")
        method, url, headers, _query_params, body, timeout_seconds, result_path = _build_api_request(arguments, runtime, data_source)
    except ValueError as e:
        return ToolResult(success=False, output="", error=str(e))

    body_format = str(runtime.get("body_format") or "json").strip().lower()
    request_kwargs: dict = {"headers": headers}
    if method == "POST" and body is not None:
        if body_format == "form":
            request_kwargs["data"] = body
        else:
            request_kwargs["json"] = body

    try:
        response = await _get_http_client().request(method, url, timeout=timeout_seconds, **request_kwargs)
        response.raise_for_status()
    except httpx.HTTPStatusError as e:
        body = e.response.text[:500]
        return ToolResult(success=False, output="", error=f"HTTP {e.response.status_code}: {body}")
    except httpx.HTTPError as e:
        return ToolResult(success=False, output="", error=f"Ошибка HTTP-клиента: {str(e)}")

    # Allow per-tool override of the response size cap via runtime config.
    runtime_max_chars = runtime.get("max_response_chars")
    if not (isinstance(runtime_max_chars, int) and runtime_max_chars > 0):
        runtime_max_chars = None
    effective_limit = runtime_max_chars or MAX_API_RESPONSE_CHARS

    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            payload = response.json()
        except ValueError:
            return ToolResult(success=False, output="", error="API вернул некорректный JSON")
        return ToolResult(success=True, output=_render_api_payload(payload, result_path, effective_limit))

    body = response.text[:effective_limit]
    if len(response.text) > effective_limit:
        body += "\n... [truncated]"
    return ToolResult(success=True, output=body)


# ============================================================
# SSH / Telnet / SNMP tools
# ============================================================

_SAFE_PARAM_RE = re.compile(r'^[a-zA-Z0-9.:\-/_@*=, ]+$')
_ANSI_ESCAPE_RE = re.compile(r'\x1b\[[0-9;]*[A-Za-z]|\x1b\].*?\x07|\r')

SSH_EXEC_TIMEOUT = 15
TELNET_EXEC_TIMEOUT = 15
SNMP_TIMEOUT = 10
MAX_CMD_OUTPUT = 8000


def _validate_cmd_param(name: str, value: str) -> str:
    """Validate a command parameter value — no shell metacharacters."""
    value = value.strip()
    if not value:
        raise ValueError(f"Пустое значение параметра '{name}'")
    if len(value) > 200:
        raise ValueError(f"Слишком длинное значение параметра '{name}'")
    if not _SAFE_PARAM_RE.match(value):
        raise ValueError(f"Недопустимые символы в параметре '{name}': {value!r}")
    return value


def _strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE_RE.sub('', text)


def _truncate_output(text: str, limit: int = MAX_CMD_OUTPUT) -> str:
    if len(text) > limit:
        return text[:limit] + "\n... [truncated]"
    return text


@dataclass
class ResolvedCommand:
    text: str                    # fully-substituted command to run
    name: str                    # command_name from the whitelist
    is_write: bool               # mutates device state (config/reboot/etc.) — guarded
    requires_confirmation: bool  # needs human approval before running (HITL)


def _resolve_command(runtime: dict, arguments: dict) -> ResolvedCommand:
    """Resolve whitelisted command template from arguments.

    A command may be marked `"write": true` in the tool's commands config; such
    commands change device state and are gated by `allow_write` (see the SSH /
    Telnet handlers).
    """
    commands = runtime.get("commands")
    if not isinstance(commands, dict) or not commands:
        raise ValueError("В конфигурации инструмента не указан commands")
    cmd_name = str(arguments.get("command_name") or "").strip()
    if not cmd_name:
        allowed = ", ".join(sorted(commands.keys()))
        raise ValueError(f"Не указан command_name. Доступные команды: {allowed}")
    cmd_cfg = commands.get(cmd_name)
    if not isinstance(cmd_cfg, dict):
        allowed = ", ".join(sorted(commands.keys()))
        raise ValueError(f"Команда '{cmd_name}' не разрешена. Доступные: {allowed}")

    template = str(cmd_cfg.get("command") or "").strip()
    if not template:
        raise ValueError(f"У команды '{cmd_name}' не указан шаблон command")

    params = arguments.get("params") or {}
    if not isinstance(params, dict):
        raise ValueError("params должен быть объектом")

    expected_params = cmd_cfg.get("params") or []
    for param_name in expected_params:
        placeholder = "{" + str(param_name) + "}"
        if placeholder in template:
            raw_value = params.get(param_name)
            if raw_value is None or str(raw_value).strip() == "":
                raise ValueError(f"Не указан обязательный параметр '{param_name}'")
            safe_value = _validate_cmd_param(param_name, str(raw_value))
            template = template.replace(placeholder, safe_value)

    return ResolvedCommand(
        text=template,
        name=cmd_name,
        is_write=bool(cmd_cfg.get("write")),
        requires_confirmation=bool(cmd_cfg.get("requires_confirmation")),
    )


async def _create_pending_action(
    tool_config: dict | None, handler_name: str, arguments: dict, resolved: ResolvedCommand,
) -> str | None:
    """Record a command awaiting human approval. Returns the pending action id
    (as str) or None if there's no chat context to attach it to.

    Stores the tool's *function* name (e.g. switch_command), not the handler
    name (ssh_exec), so approval can reload the exact tool config to re-execute.
    """
    import uuid as _uuid
    ctx = (tool_config or {}).get("_context") or {}
    tid, cid = ctx.get("tenant_id"), ctx.get("chat_id")
    if not tid or not cid:
        return None
    fn = (tool_config or {}).get("function") or {}
    tool_name = fn.get("name") or handler_name
    try:
        from app.core.database import async_session
        from app.models.pending_tool_action import PendingToolAction
        action_id = _uuid.uuid4()
        async with async_session() as db:
            db.add(PendingToolAction(
                id=action_id,
                tenant_id=_uuid.UUID(tid),
                chat_id=_uuid.UUID(cid),
                message_id=_uuid.UUID(ctx["user_message_id"]) if ctx.get("user_message_id") else None,
                tool_name=tool_name,
                command_name=resolved.name,
                command_text=resolved.text,
                arguments=arguments,
            ))
            await db.commit()
        return str(action_id)
    except Exception:
        logger.exception("failed to create pending tool action for %s/%s", tool_name, resolved.name)
        return None


async def _audit_write_command(
    tool_config: dict | None, tool_name: str, host: str, resolved: ResolvedCommand, outcome: str,
) -> None:
    """Record a state-changing command attempt to the admin audit log.

    `outcome` ∈ {executed, blocked, failed}. Best-effort: a failed audit must
    never block the tool. Runs in its own session (RLS bypass on insert)."""
    import uuid as _uuid
    ctx = (tool_config or {}).get("_context") or {}
    tid = ctx.get("tenant_id")
    akid = ctx.get("api_key_id")
    try:
        from app.core.database import async_session
        from app.models.admin_audit_log import AdminAuditLog
        async with async_session() as db:
            db.add(AdminAuditLog(
                actor_id=_uuid.UUID(akid) if akid else None,
                actor_role="tenant_api_key",
                action="tool.write_command",
                resource_type=tool_name,
                resource_id=(host or "")[:255],
                tenant_id=_uuid.UUID(tid) if tid else None,
                after_json={
                    "command_name": resolved.name,
                    "command": resolved.text,
                    "outcome": outcome,
                    "chat_id": ctx.get("chat_id"),
                },
            ))
            await db.commit()
    except Exception:
        logger.exception("write-command audit failed (non-fatal) for %s/%s", tool_name, resolved.name)


def _write_blocked(runtime: dict, resolved: ResolvedCommand) -> bool:
    """A write command runs only if the tool opts in with allow_write=true."""
    return resolved.is_write and not bool(runtime.get("allow_write"))


async def _resolve_net_credentials(runtime: dict) -> dict:
    """Load and return data source connection info."""
    ds_id = runtime.get("data_source_id")
    if not ds_id:
        # Inline credentials in runtime
        host = str(runtime.get("host") or "").strip()
        if not host:
            raise ValueError("Не указан host (ни data_source_id, ни host)")
        return {
            "host": host,
            "port": runtime.get("port"),
            "username": str(runtime.get("username") or "").strip(),
            "password": str(runtime.get("password") or "").strip(),
        }
    ds = await _load_tenant_data_source(str(ds_id))
    config = ds.get("config_json") or {}
    secret = ds.get("secret_json") or {}
    return {
        "host": str(config.get("host") or "").strip(),
        "port": config.get("port"),
        "username": str(config.get("username") or secret.get("username") or "").strip(),
        "password": str(secret.get("password") or "").strip(),
        "private_key": str(secret.get("private_key") or "").strip(),
        "community": str(secret.get("community") or "").strip(),
        "snmp_version": str(config.get("snmp_version") or config.get("version") or "2c").strip(),
        "init_commands": list(config.get("init_commands") or []),
    }


# ---- SSH ----

@register_tool("ssh_exec")
async def tool_ssh_exec(arguments: dict, tool_config: dict | None = None) -> ToolResult:
    """
    Execute a whitelisted command on a remote host via SSH.

    Designed for Linux servers (CentOS/AlmaLinux) and Juniper devices.
    The admin defines allowed commands in x_backend_config.commands.
    The model chooses command_name and provides params — never raw commands.
    """
    runtime = _extract_runtime_config(tool_config)

    try:
        resolved = _resolve_command(runtime, arguments)
        creds = await _resolve_net_credentials(runtime)
    except ValueError as e:
        return ToolResult(success=False, output="", error=str(e))

    command = resolved.text
    host = creds["host"]
    port = int(creds.get("port") or 22)
    username = creds.get("username")
    password = creds.get("password")
    private_key = creds.get("private_key")
    timeout = min(int(runtime.get("timeout_seconds") or SSH_EXEC_TIMEOUT), 30)

    if not host:
        return ToolResult(success=False, output="", error="Не указан host")
    if not username:
        return ToolResult(success=False, output="", error="Не указан username")

    # Write-command guardrail: state-changing commands are blocked unless the
    # tool explicitly opts in (allow_write). Every write attempt is audited.
    if _write_blocked(runtime, resolved):
        await _audit_write_command(tool_config, "ssh_exec", host, resolved, "blocked")
        return ToolResult(
            success=False, output="",
            error=(f"Команда '{resolved.name}' изменяет состояние устройства и заблокирована. "
                   "Для выполнения write-команд включите allow_write в конфигурации инструмента."),
        )

    # Human-in-the-loop: a command marked requires_confirmation is not run by the
    # model; it's parked for explicit approval (executed later with _approved).
    if resolved.requires_confirmation and not runtime.get("_approved"):
        action_id = await _create_pending_action(tool_config, "ssh_exec", arguments, resolved)
        return ToolResult(
            success=True,
            output=(f"⏸ Команда '{resolved.name}' требует подтверждения пользователя"
                    + (f" (запрос #{action_id})." if action_id else ".")
                    + " Сообщи пользователю, что нужно подтвердить действие в интерфейсе — "
                      "оно НЕ выполнено."),
        )

    try:
        import asyncssh

        connect_kwargs: dict = {
            "host": host,
            "port": port,
            "username": username,
            "known_hosts": None,
            "connect_timeout": timeout,
        }
        if private_key:
            connect_kwargs["client_keys"] = [asyncssh.import_private_key(private_key)]
        elif password:
            connect_kwargs["password"] = password
        else:
            return ToolResult(success=False, output="", error="Не указан password или private_key")

        async with asyncssh.connect(**connect_kwargs) as conn:
            # Prepend init commands from data source config
            init_commands = creds.get("init_commands") or []
            full_command = " && ".join([*[str(c) for c in init_commands], command]) if init_commands else command
            result = await asyncio.wait_for(conn.run(full_command), timeout=timeout)
            stdout = result.stdout or ""
            stderr = result.stderr or ""
            exit_code = result.exit_status

        output = stdout.strip()
        if stderr.strip():
            output += f"\n--- stderr ---\n{stderr.strip()}"
        if exit_code and exit_code != 0:
            output += f"\n[exit code: {exit_code}]"

        if runtime.get("strip_ansi", True):
            output = _strip_ansi(output)

        if resolved.is_write:
            await _audit_write_command(tool_config, "ssh_exec", host, resolved, "executed")
        return ToolResult(success=True, output=_truncate_output(output))

    except asyncssh.Error as e:
        return ToolResult(success=False, output="", error=f"SSH ошибка: {str(e)[:300]}")
    except asyncio.TimeoutError:
        return ToolResult(success=False, output="", error=f"SSH таймаут ({timeout}с)")
    except Exception as e:
        logger.exception("SSH exec error for %s", host)
        return ToolResult(success=False, output="", error=f"Ошибка: {str(e)[:300]}")


# ---- Telnet ----

# Default prompt patterns for common equipment
_TELNET_PROMPTS = {
    "dlink": r'[#>]\s*$',
    "bdcom": r'[#>]\s*$',
    "generic": r'[#>$]\s*$',
    "juniper": r'[#>]\s*$',
}


@register_tool("telnet_exec")
async def tool_telnet_exec(arguments: dict, tool_config: dict | None = None) -> ToolResult:
    """
    Execute a whitelisted command on a remote device via Telnet.

    Designed for D-Link DES/DGS switches and BDCOM OLTs.
    Login sequence: wait for Username/login prompt, send username,
    wait for Password prompt, send password, wait for CLI prompt,
    then execute the command.
    """
    runtime = _extract_runtime_config(tool_config)

    try:
        resolved = _resolve_command(runtime, arguments)
        creds = await _resolve_net_credentials(runtime)
    except ValueError as e:
        return ToolResult(success=False, output="", error=str(e))

    command = resolved.text
    host = creds["host"]
    port = int(creds.get("port") or 23)
    username = creds.get("username") or ""
    password = creds.get("password") or ""
    timeout = min(int(runtime.get("timeout_seconds") or TELNET_EXEC_TIMEOUT), 30)

    # Write-command guardrail (see ssh_exec): block state-changing commands
    # unless allow_write is set; audit every write attempt.
    if _write_blocked(runtime, resolved):
        await _audit_write_command(tool_config, "telnet_exec", host, resolved, "blocked")
        return ToolResult(
            success=False, output="",
            error=(f"Команда '{resolved.name}' изменяет состояние устройства и заблокирована. "
                   "Для выполнения write-команд включите allow_write в конфигурации инструмента."),
        )

    # Human-in-the-loop confirmation (see ssh_exec).
    if resolved.requires_confirmation and not runtime.get("_approved"):
        action_id = await _create_pending_action(tool_config, "telnet_exec", arguments, resolved)
        return ToolResult(
            success=True,
            output=(f"⏸ Команда '{resolved.name}' требует подтверждения пользователя"
                    + (f" (запрос #{action_id})." if action_id else ".")
                    + " Сообщи пользователю, что нужно подтвердить действие в интерфейсе — "
                      "оно НЕ выполнено."),
        )

    vendor = str(runtime.get("vendor") or "generic").strip().lower()
    prompt_pattern = str(runtime.get("prompt_pattern") or _TELNET_PROMPTS.get(vendor) or _TELNET_PROMPTS["generic"])
    login_prompt = str(runtime.get("login_prompt") or r'(?i)(user\s*name|login|username)\s*:\s*$')
    password_prompt = str(runtime.get("password_prompt") or r'(?i)pass\s*word\s*:\s*$')
    # Pagination disabling commands for different vendors
    pager_disable = runtime.get("pager_disable")
    if pager_disable is None:
        pager_disable_map = {
            "dlink": "disable clipaging",
            "bdcom": "terminal length 0",
        }
        pager_disable = pager_disable_map.get(vendor)

    if not host:
        return ToolResult(success=False, output="", error="Не указан host")

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=timeout,
        )
    except (asyncio.TimeoutError, OSError) as e:
        return ToolResult(success=False, output="", error=f"Не удалось подключиться к {host}:{port}: {e}")

    buffer = ""
    try:
        async def _read_until(pattern: str, read_timeout: float = 10.0) -> str:
            nonlocal buffer
            compiled = re.compile(pattern)
            deadline = asyncio.get_event_loop().time() + read_timeout
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    raise asyncio.TimeoutError()
                try:
                    chunk = await asyncio.wait_for(reader.read(4096), timeout=remaining)
                except asyncio.TimeoutError:
                    raise
                if not chunk:
                    raise ConnectionError("Соединение закрыто")
                buffer += chunk.decode("utf-8", errors="replace")
                if compiled.search(buffer):
                    result = buffer
                    buffer = ""
                    return result

        # Handle telnet option negotiations (IAC)
        async def _send(text: str):
            writer.write((text + "\r\n").encode("utf-8"))
            await writer.drain()

        # Login sequence
        if username:
            await _read_until(login_prompt, read_timeout=timeout)
            await _send(username)

        if password:
            await _read_until(password_prompt, read_timeout=timeout)
            await _send(password)

        # Wait for CLI prompt
        await _read_until(prompt_pattern, read_timeout=timeout)

        # Run init commands from data source config (enable, cli, etc.)
        init_commands = creds.get("init_commands") or []
        for init_cmd in init_commands:
            await _send(str(init_cmd))
            await _read_until(prompt_pattern, read_timeout=timeout)

        # Disable pagination if configured
        if pager_disable:
            await _send(pager_disable)
            await _read_until(prompt_pattern, read_timeout=5)

        # Execute the actual command
        await _send(command)
        output = await _read_until(prompt_pattern, read_timeout=timeout)

        # Clean up: remove the echoed command and trailing prompt
        lines = output.split("\n")
        # Remove first line (echoed command) and last line (prompt)
        if lines and command.strip() in lines[0]:
            lines = lines[1:]
        if lines and re.search(prompt_pattern, lines[-1]):
            lines = lines[:-1]
        output = "\n".join(lines).strip()

        if runtime.get("strip_ansi", True):
            output = _strip_ansi(output)

        if resolved.is_write:
            await _audit_write_command(tool_config, "telnet_exec", host, resolved, "executed")
        return ToolResult(success=True, output=_truncate_output(output))

    except asyncio.TimeoutError:
        return ToolResult(success=False, output="", error=f"Telnet таймаут ({timeout}с)")
    except Exception as e:
        logger.exception("Telnet exec error for %s", host)
        return ToolResult(success=False, output="", error=f"Ошибка telnet: {str(e)[:300]}")
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


# ---- SNMP ----

@register_tool("snmp_get")
async def tool_snmp_get(arguments: dict, tool_config: dict | None = None) -> ToolResult:
    """
    Query whitelisted SNMP OIDs from a network device.

    Supports both GET (single OID) and WALK (subtree).
    Designed for D-Link and BDCOM equipment.
    """
    runtime = _extract_runtime_config(tool_config)

    try:
        creds = await _resolve_net_credentials(runtime)
    except ValueError as e:
        return ToolResult(success=False, output="", error=str(e))

    host = creds["host"]
    port = int(creds.get("port") or 161)
    community = creds.get("community") or "public"
    timeout_sec = min(int(runtime.get("timeout_seconds") or SNMP_TIMEOUT), 30)

    if not host:
        return ToolResult(success=False, output="", error="Не указан host")

    # Determine operation mode and resolve OID
    oid_name = str(arguments.get("oid_name") or "").strip()
    operation = str(arguments.get("operation") or "get").strip().lower()

    oids_cfg = runtime.get("oids") or {}
    walk_oids_cfg = runtime.get("walk_oids") or {}
    all_oids = {**oids_cfg, **walk_oids_cfg}

    if not oid_name:
        available = ", ".join(sorted(all_oids.keys()))
        return ToolResult(success=False, output="", error=f"Не указан oid_name. Доступные: {available}")

    # Check if it's a walk OID
    if oid_name in walk_oids_cfg:
        operation = "walk"
        oid_cfg = walk_oids_cfg[oid_name]
    elif oid_name in oids_cfg:
        oid_cfg = oids_cfg[oid_name]
    else:
        available = ", ".join(sorted(all_oids.keys()))
        return ToolResult(success=False, output="", error=f"OID '{oid_name}' не разрешён. Доступные: {available}")

    if not isinstance(oid_cfg, dict):
        return ToolResult(success=False, output="", error=f"Некорректная конфигурация OID '{oid_name}'")

    oid_template = str(oid_cfg.get("oid") or "").strip()
    if not oid_template:
        return ToolResult(success=False, output="", error=f"У OID '{oid_name}' не указан oid")

    # Substitute params into OID template
    params = arguments.get("params") or {}
    if not isinstance(params, dict):
        params = {}
    for param_name in oid_cfg.get("params") or []:
        placeholder = "{" + str(param_name) + "}"
        if placeholder in oid_template:
            raw_value = params.get(param_name)
            if raw_value is None or str(raw_value).strip() == "":
                return ToolResult(success=False, output="", error=f"Не указан параметр '{param_name}'")
            val = str(raw_value).strip()
            if not re.match(r'^[0-9.]+$', val):
                return ToolResult(success=False, output="", error=f"Параметр '{param_name}' должен содержать только цифры и точки")
            oid_template = oid_template.replace(placeholder, val)

    try:
        from pysnmp.hlapi.v3arch.asyncio import (
            CommunityData,
            SnmpEngine,
            UdpTransportTarget,
            ContextData,
            ObjectType,
            ObjectIdentity,
            get_cmd,
            bulk_cmd,
        )

        engine = SnmpEngine()
        community_data = CommunityData(community, mpModel=1)  # SNMPv2c
        transport = await UdpTransportTarget.create((host, port), timeout=timeout_sec, retries=1)
        context = ContextData()

        if operation == "walk":
            # SNMP GETBULK walk
            results = []
            base_oid = oid_template
            max_rows = int(runtime.get("walk_max_rows") or 256)

            oid_obj = ObjectIdentity(base_oid)
            while len(results) < max_rows:
                error_indication, error_status, error_index, var_binds = await bulk_cmd(
                    engine, community_data, transport, context,
                    0, 25,  # nonRepeaters=0, maxRepetitions=25
                    ObjectType(oid_obj),
                )
                if error_indication:
                    return ToolResult(success=False, output="", error=f"SNMP ошибка: {error_indication}")
                if error_status:
                    return ToolResult(success=False, output="", error=f"SNMP ошибка: {error_status.prettyPrint()}")

                for var_bind in var_binds:
                    oid_str = str(var_bind[0])
                    if not oid_str.startswith(base_oid):
                        # Walked past the subtree
                        break
                    results.append(f"{oid_str} = {var_bind[1].prettyPrint()}")
                    oid_obj = var_bind[0]
                else:
                    continue
                break

            if not results:
                return ToolResult(success=True, output="SNMP walk: пусто (нет данных)")
            output = f"SNMP walk {base_oid} ({len(results)} записей):\n" + "\n".join(results)
            return ToolResult(success=True, output=_truncate_output(output))

        else:
            # SNMP GET
            error_indication, error_status, error_index, var_binds = await get_cmd(
                engine, community_data, transport, context,
                ObjectType(ObjectIdentity(oid_template)),
            )
            if error_indication:
                return ToolResult(success=False, output="", error=f"SNMP ошибка: {error_indication}")
            if error_status:
                return ToolResult(success=False, output="", error=f"SNMP ошибка: {error_status.prettyPrint()}")

            parts = []
            for var_bind in var_binds:
                parts.append(f"{var_bind[0].prettyPrint()} = {var_bind[1].prettyPrint()}")
            output = "\n".join(parts) if parts else "Нет данных"

            # Apply value_map if configured
            value_map = oid_cfg.get("value_map")
            if isinstance(value_map, dict) and len(var_binds) == 1:
                raw_val = str(var_binds[0][1])
                mapped = value_map.get(raw_val)
                if mapped:
                    output += f" ({mapped})"

            return ToolResult(success=True, output=output)

    except ImportError:
        return ToolResult(success=False, output="", error="pysnmp не установлен")
    except asyncio.TimeoutError:
        return ToolResult(success=False, output="", error=f"SNMP таймаут ({timeout_sec}с)")
    except Exception as e:
        logger.exception("SNMP error for %s", host)
        return ToolResult(success=False, output="", error=f"SNMP ошибка: {str(e)[:300]}")


@register_tool("recall_chat")
async def recall_chat_handler(arguments: dict, tool_config: dict | None = None) -> ToolResult:
    """Semantic search over chat-resume embeddings — find past Q→A pairs
    relevant to the current query. Returns short list; full content is fetched
    via get_message."""
    import uuid as _uuid
    from sqlalchemy import select, text as sa_text
    from app.core.database import async_session
    from app.core.config import settings as app_settings
    from app.models.message import Message
    from app.models.tenant_shell_config import TenantShellConfig
    from app.providers.factory import get_provider

    ctx = (tool_config or {}).get("_context") or {}
    tenant_id_s = ctx.get("tenant_id")
    chat_id_s = ctx.get("chat_id")
    if not tenant_id_s:
        return ToolResult(success=False, output="", error="recall_chat: tenant context missing")

    query = (arguments.get("query") or "").strip()
    if not query:
        return ToolResult(success=False, output="", error="recall_chat: 'query' is required")
    limit = max(1, min(int(arguments.get("limit") or 5), 20))
    scope = (arguments.get("scope") or "chat").strip()

    try:
        async with async_session() as db:
            cfg = (await db.execute(
                select(TenantShellConfig).where(TenantShellConfig.tenant_id == _uuid.UUID(tenant_id_s))
            )).scalar_one_or_none()
            if not cfg or not cfg.embedding_model_name:
                return ToolResult(success=False, output="", error="recall_chat: no embedding_model_name configured")
            if scope == "tenant" and not getattr(cfg, "recall_cross_chat_enabled", False):
                scope = "chat"  # cross-chat disabled by tenant policy
            provider = get_provider("ollama", app_settings.OLLAMA_BASE_URL or "http://localhost:11434")
            vectors = await provider.embed(query, cfg.embedding_model_name)
            if not vectors:
                return ToolResult(success=False, output="", error="recall_chat: embedding failed")
            qvec = vectors[0]
            # asyncpg doesn't auto-cast list → vector. Serialize to pgvector text form.
            qvec_str = "[" + ",".join(f"{float(x):.6f}" for x in qvec) + "]"

            # Vector search via pgvector cosine distance.
            params = {"tid": _uuid.UUID(tenant_id_s), "qvec": qvec_str, "limit": limit}
            scope_clause = ""
            if scope == "chat":
                params["cid"] = _uuid.UUID(chat_id_s) if chat_id_s else None
                scope_clause = "AND m_user.chat_id = :cid"
            # Hybrid recall: rank by the BEST (smallest cosine distance) of
            #  - resume_embedding  (sanitized summary → topical match)
            #  - content_embedding (raw Q+A → factual match: IPs, names, numbers)
            # so factual queries that the summary strips still hit. We still
            # RETURN the sanitized resume text — concrete values come via
            # get_message, never from the (possibly distorted) summary.
            # COALESCE missing vectors to distance 2 (max cosine distance) so a
            # row missing one embedding ranks purely on the other.
            sql = sa_text(f"""
                SELECT
                    m_user.id::text AS user_id,
                    m_user.resume_query,
                    m_user.created_at,
                    1 - LEAST(
                        COALESCE(m_user.resume_embedding  <=> CAST(:qvec AS vector), 2),
                        COALESCE(m_user.content_embedding <=> CAST(:qvec AS vector), 2)
                    ) AS similarity,
                    m_asst.id::text AS asst_id,
                    m_asst.resume_response
                FROM messages m_user
                LEFT JOIN LATERAL (
                    SELECT id, resume_response FROM messages
                    WHERE chat_id = m_user.chat_id
                      AND role = 'assistant'
                      AND created_at >= m_user.created_at
                    ORDER BY created_at ASC LIMIT 1
                ) m_asst ON true
                WHERE m_user.tenant_id = :tid
                  AND m_user.role = 'user'
                  AND (m_user.resume_embedding IS NOT NULL OR m_user.content_embedding IS NOT NULL)
                  {scope_clause}
                ORDER BY LEAST(
                    COALESCE(m_user.resume_embedding  <=> CAST(:qvec AS vector), 2),
                    COALESCE(m_user.content_embedding <=> CAST(:qvec AS vector), 2)
                )
                LIMIT :limit
            """)
            rows = (await db.execute(sql, params)).fetchall()

        if not rows:
            return ToolResult(success=True, output="(резюмированных записей по запросу не найдено)")
        lines = [f"Найдено {len(rows)} (scope={scope}):"]
        for r in rows:
            sim = f"{r.similarity:.2f}" if r.similarity is not None else "—"
            ts = r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "—"
            lines.append(
                f"- [{r.user_id}] sim={sim} {ts}\n"
                f"  Q: {(r.resume_query or '(нет резюме)').strip()}\n"
                f"  A: {(r.resume_response or '(нет резюме)').strip()}"
            )
        # Q/A здесь — РЕЗЮМЕ без конкретных значений (IP, числа, имена вырезаны
        # специально). За точными данными вызови get_message(id="<id из скобок>")
        # — он вернёт полный исходный текст вопроса и ответа.
        lines.append(
            "\nQ/A выше — резюме без конкретики. Полный текст пары: "
            "get_message(id=\"<id из [...]>\")."
        )
        return ToolResult(success=True, output="\n".join(lines))
    except Exception as e:
        logger.exception("recall_chat failed")
        return ToolResult(success=False, output="", error=f"recall_chat: {str(e)[:200]}")


@register_tool("get_message")
async def get_message_handler(arguments: dict, tool_config: dict | None = None) -> ToolResult:
    """Fetch the FULL user content + matching assistant reply for a message id."""
    import uuid as _uuid
    from sqlalchemy import select
    from app.core.database import async_session
    from app.models.message import Message

    ctx = (tool_config or {}).get("_context") or {}
    tenant_id_s = ctx.get("tenant_id")
    if not tenant_id_s:
        return ToolResult(success=False, output="", error="get_message: tenant context missing")

    mid_s = (arguments.get("id") or "").strip()
    if not mid_s:
        return ToolResult(success=False, output="", error="get_message: 'id' is required")
    # Model may copy the bracketed token verbatim — strip the msg: prefix if present.
    if mid_s.startswith("msg:"):
        mid_s = mid_s[4:].strip()
    try:
        mid = _uuid.UUID(mid_s)
    except ValueError:
        return ToolResult(success=False, output="", error=f"get_message: invalid id '{mid_s}'")

    try:
        async with async_session() as db:
            msg = (await db.execute(
                select(Message).where(Message.id == mid, Message.tenant_id == _uuid.UUID(tenant_id_s))
            )).scalar_one_or_none()
            if not msg:
                return ToolResult(success=False, output="", error=f"get_message: id {mid_s} not found")
            # If the id points to a user message — also fetch matching assistant reply.
            assistant_text: str | None = None
            if msg.role == "user":
                asst = (await db.execute(
                    select(Message).where(
                        Message.chat_id == msg.chat_id,
                        Message.role == "assistant",
                        Message.created_at >= msg.created_at,
                    ).order_by(Message.created_at.asc()).limit(1)
                )).scalar_one_or_none()
                if asst:
                    assistant_text = asst.content

        # Resolve artifacts: live on the assistant row.
        artifacts_list = None
        if msg.role == "assistant":
            artifacts_list = msg.artifacts
        elif msg.role == "user":
            # If we already fetched matching assistant above, reuse its artifacts.
            try:
                artifacts_list = asst.artifacts if asst else None  # type: ignore[name-defined]
            except NameError:
                artifacts_list = None

        parts = [
            f"id: {mid_s}",
            f"role: {msg.role}",
            f"created_at: {msg.created_at}",
            f"content:\n{msg.content}",
        ]
        if assistant_text:
            parts.append(f"\n---\nassistant_reply:\n{assistant_text}")
        if artifacts_list:
            arts_lines = []
            for a in artifacts_list:
                kind = (a.get("kind") or "code").strip()
                label = (a.get("label") or "").strip()
                lang = (a.get("lang") or "").strip()
                arts_lines.append(f"- [{kind}] {label}" + (f" (lang={lang})" if lang else ""))
            parts.append("\n---\nartifacts:\n" + "\n".join(arts_lines))
        return ToolResult(success=True, output="\n".join(parts))
    except Exception as e:
        logger.exception("get_message failed")
        return ToolResult(success=False, output="", error=f"get_message: {str(e)[:200]}")


@register_tool("find_artifacts")
async def find_artifacts_handler(arguments: dict, tool_config: dict | None = None) -> ToolResult:
    """Search the first-class artifacts table for code/configs/scripts/SQL.
    Returns a list of {artifact_id, kind, label, created_at, similarity?}.
    Caller fetches full content via get_artifact(id)."""
    import uuid as _uuid
    from sqlalchemy import select, text as sa_text
    from app.core.database import async_session
    from app.core.config import settings as app_settings
    from app.models.tenant_shell_config import TenantShellConfig
    from app.providers.factory import get_provider

    ctx = (tool_config or {}).get("_context") or {}
    tenant_id_s = ctx.get("tenant_id")
    chat_id_s = ctx.get("chat_id")
    if not tenant_id_s:
        return ToolResult(success=False, output="", error="find_artifacts: tenant context missing")

    kind = (arguments.get("kind") or "").strip().lower() or None
    query = (arguments.get("query") or "").strip() or None
    limit = max(1, min(int(arguments.get("limit") or 10), 30))
    scope = (arguments.get("scope") or "chat").strip()
    if scope not in ("chat", "tenant"):
        scope = "chat"

    try:
        async with async_session() as db:
            cfg = (await db.execute(
                select(TenantShellConfig).where(TenantShellConfig.tenant_id == _uuid.UUID(tenant_id_s))
            )).scalar_one_or_none()
            if scope == "tenant" and not (cfg and getattr(cfg, "recall_cross_chat_enabled", False)):
                scope = "chat"  # cross-chat blocked by tenant policy

            # Query embedding — semantic ranking when both query and tenant
            # embedding model are present. Otherwise fall back to recency.
            qvec_str: str | None = None
            if query and cfg and cfg.embedding_model_name:
                try:
                    provider = get_provider("ollama", app_settings.OLLAMA_BASE_URL or "http://localhost:11434")
                    vectors = await provider.embed(query, cfg.embedding_model_name)
                    if vectors:
                        qvec_str = "[" + ",".join(f"{float(x):.6f}" for x in vectors[0]) + "]"
                except Exception:
                    logger.exception("find_artifacts: embed failed; falling back to recency")

            params: dict = {"tid": _uuid.UUID(tenant_id_s), "limit": limit}
            where_clauses = ["a.tenant_id = :tid", "a.deleted_at IS NULL"]
            if scope == "chat":
                if not chat_id_s:
                    return ToolResult(success=False, output="", error="find_artifacts: chat context missing for scope=chat")
                params["cid"] = _uuid.UUID(chat_id_s)
                where_clauses.append("a.chat_id = :cid")
            if kind:
                params["kind"] = kind
                where_clauses.append("lower(a.kind) = :kind")

            select_extra = ""
            order_clause = "ORDER BY a.created_at DESC"
            if qvec_str:
                params["qvec"] = qvec_str
                select_extra = ", (1 - (a.embedding <=> CAST(:qvec AS vector))) AS similarity"
                # Artifacts without embeddings still appear (NULLS LAST), but ranked behind.
                order_clause = "ORDER BY similarity DESC NULLS LAST, a.created_at DESC"

            where_sql = " AND ".join(where_clauses)
            sql = sa_text(f"""
                SELECT
                    a.id::text AS artifact_id,
                    a.kind, a.label, a.lang,
                    a.version, a.tokens_estimate, a.created_at
                    {select_extra}
                FROM artifacts a
                WHERE {where_sql}
                {order_clause}
                LIMIT :limit
            """)
            rows = (await db.execute(sql, params)).fetchall()

        if not rows:
            conds = []
            if kind: conds.append(f"kind={kind}")
            if query: conds.append(f"query={query!r}")
            conds.append(f"scope={scope}")
            return ToolResult(success=True, output=f"(артефактов не найдено: {', '.join(conds)})")

        header = f"Найдено {len(rows)} артефакт(ов) (scope={scope}"
        if kind: header += f", kind={kind}"
        header += "):"
        lines = [header]
        for r in rows:
            ts = r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "—"
            sim_str = ""
            if qvec_str and getattr(r, "similarity", None) is not None:
                sim_str = f" sim={r.similarity:.2f}"
            lang_str = f" (lang={r.lang})" if r.lang else ""
            lines.append(
                f"- [{r.artifact_id}] [{r.kind}] {r.label}{lang_str}\n"
                f"  v{r.version}, ~{r.tokens_estimate} tok, {ts}{sim_str}"
            )
        lines.append("\nЧтобы получить полный текст артефакта — вызови get_artifact(id).")
        return ToolResult(success=True, output="\n".join(lines))
    except Exception as e:
        logger.exception("find_artifacts failed")
        return ToolResult(success=False, output="", error=f"find_artifacts: {str(e)[:200]}")


@register_tool("memory_save")
async def memory_save_handler(arguments: dict, tool_config: dict | None = None) -> ToolResult:
    """Save a memory entry that will be re-injected into future chat contexts.
    Context (tenant_id, chat_id) is taken from tool_config['_context'] injected by pipeline."""
    import uuid as _uuid
    from app.core.database import async_session
    from app.models.memory_entry import MemoryEntry
    from app.services.memory.embedder import embed_memory_entry

    ctx = (tool_config or {}).get("_context") or {}
    tenant_id_s = ctx.get("tenant_id")
    chat_id_s = ctx.get("chat_id")
    if not tenant_id_s:
        return ToolResult(success=False, output="", error="memory_save: tenant context missing")

    content = (arguments.get("content") or "").strip()
    if not content:
        return ToolResult(success=False, output="", error="memory_save: 'content' is required")
    memory_type = (arguments.get("memory_type") or "long_term").strip()
    if memory_type not in ("long_term", "episodic", "fact", "preference"):
        memory_type = "long_term"
    is_pinned = bool(arguments.get("is_pinned", False))
    scope = (arguments.get("scope") or "chat").strip()  # "chat" or "tenant"
    priority = int(arguments.get("priority") or 1)

    try:
        async with async_session() as db:
            m = MemoryEntry(
                tenant_id=_uuid.UUID(tenant_id_s),
                chat_id=_uuid.UUID(chat_id_s) if (scope == "chat" and chat_id_s) else None,
                content=content[:2000],
                memory_type=memory_type,
                is_pinned=is_pinned,
                priority=priority,
            )
            db.add(m)
            await db.commit()
            await db.refresh(m)
            new_id = m.id

        # Embed in background (do not block the tool response)
        asyncio.create_task(embed_memory_entry(new_id))

        return ToolResult(
            success=True,
            output=(
                f"Memory entry saved.\n"
                f"  id: {new_id}\n  type: {memory_type}\n  pinned: {is_pinned}\n"
                f"  scope: {scope}\n  content: {content[:80]}{'...' if len(content) > 80 else ''}"
            ),
        )
    except Exception as e:
        logger.exception("memory_save failed")
        return ToolResult(success=False, output="", error=f"Не удалось сохранить: {str(e)[:200]}")


@register_tool("get_artifact")
async def get_artifact_handler(arguments: dict, tool_config: dict | None = None) -> ToolResult:
    """Fetch the verbatim content of an artifact by id (from the artifacts
    table — the immutable source of truth). Use this whenever the user asks
    about details of a script/SQL/config that was produced earlier — never
    answer from the resume/history block, those don't carry concrete values."""
    import uuid as _uuid
    from sqlalchemy import select, update
    from datetime import datetime, timezone
    from app.core.database import async_session
    from app.models.artifact import Artifact

    ctx = (tool_config or {}).get("_context") or {}
    tenant_id_s = ctx.get("tenant_id")
    if not tenant_id_s:
        return ToolResult(success=False, output="", error="get_artifact: tenant context missing")

    aid_s = (arguments.get("id") or "").strip()
    if not aid_s:
        return ToolResult(success=False, output="", error="get_artifact: 'id' is required")
    # Defensive: model occasionally pastes a `msg:<uuid>` token here instead of
    # an artifact_id — fail fast with an informative error pointing at the
    # right tool, rather than the cryptic "not found".
    if aid_s.startswith("msg:"):
        return ToolResult(
            success=False, output="",
            error=(
                f"get_artifact: id '{aid_s}' начинается с 'msg:' — это id СООБЩЕНИЯ, "
                "не артефакта. Вызови `get_message(id=\"<uuid-без-префикса>\")` "
                "для полного текста обмена. Для артефакта используй id из "
                "маркера 📎 (artifact_id=...)."
            ),
        )
    try:
        aid = _uuid.UUID(aid_s)
    except ValueError:
        return ToolResult(success=False, output="", error=f"get_artifact: invalid id '{aid_s}'")

    try:
        async with async_session() as db:
            art = (await db.execute(
                select(Artifact).where(
                    Artifact.id == aid,
                    Artifact.tenant_id == _uuid.UUID(tenant_id_s),
                )
            )).scalar_one_or_none()
            if not art:
                return ToolResult(success=False, output="", error=f"get_artifact: id {aid_s} not found")
            if art.deleted_at is not None:
                return ToolResult(success=False, output="", error=f"get_artifact: id {aid_s} deleted")
            # Touch — pulling an artifact via tool is a clear signal it's hot.
            await db.execute(
                update(Artifact)
                .where(Artifact.id == aid)
                .values(last_referenced_at=datetime.now(timezone.utc))
            )
            await db.commit()

        header_lines = [
            f"id: {art.id}",
            f"kind: {art.kind}",
            f"label: {art.label}",
            f"lang: {art.lang or '—'}",
            f"version: v{art.version}",
            f"source_message_id: {art.source_message_id or '—'}",
            f"tokens_estimate: {art.tokens_estimate}",
            f"created_at: {art.created_at}",
        ]
        fence_lang = art.lang or ""
        body = f"```{fence_lang}\n{art.content}\n```"
        return ToolResult(success=True, output="\n".join(header_lines) + "\n\n" + body)
    except Exception as e:
        logger.exception("get_artifact failed")
        return ToolResult(success=False, output="", error=f"get_artifact: {str(e)[:200]}")


@register_tool("search_kb")
async def search_kb_handler(arguments: dict, tool_config: dict | None = None) -> ToolResult:
    """Semantic search over the tenant's Knowledge Base — wider/looser than
    the auto-grounded KB block. Use when the auto-grounded excerpts are off
    or absent. Returns short summaries with doc title + source + relevance."""
    import uuid as _uuid
    from sqlalchemy import select
    from app.core.database import async_session
    from app.core.config import settings as app_settings
    from app.models.tenant_shell_config import TenantShellConfig
    from app.providers.factory import get_provider
    from app.services.kb.embedder import search_kb_chunks

    ctx = (tool_config or {}).get("_context") or {}
    tenant_id_s = ctx.get("tenant_id")
    if not tenant_id_s:
        return ToolResult(success=False, output="", error="search_kb: tenant context missing")

    query = (arguments.get("query") or "").strip()
    if not query:
        return ToolResult(success=False, output="", error="search_kb: 'query' is required")
    limit = max(1, min(int(arguments.get("limit") or 5), 15))

    try:
        async with async_session() as db:
            cfg = (await db.execute(
                select(TenantShellConfig).where(TenantShellConfig.tenant_id == _uuid.UUID(tenant_id_s))
            )).scalar_one_or_none()
            if not cfg or not cfg.embedding_model_name:
                return ToolResult(success=False, output="", error="search_kb: no embedding_model_name configured")
            if not cfg.knowledge_base_enabled:
                return ToolResult(success=False, output="", error="search_kb: KB disabled for this tenant")
            provider = get_provider("ollama", app_settings.OLLAMA_BASE_URL or "http://localhost:11434")
            chunks = await search_kb_chunks(
                tenant_id=tenant_id_s,
                query=query,
                db=db,
                provider=provider,
                embedding_model=cfg.embedding_model_name,
                max_results=limit,
            )
        if not chunks:
            return ToolResult(success=True, output=f"(в KB ничего не найдено по запросу: {query!r})")

        MAX_CONTENT_CHARS = 600
        lines = [f"Найдено {len(chunks)} фрагмент(ов) в KB:"]
        for c in chunks:
            doc = getattr(c, "doc_title", None) or "(без названия)"
            src = getattr(c, "source_url", None) or ""
            src_type = getattr(c, "source_type", None) or "manual"
            content = (c.content or "").strip()
            if len(content) > MAX_CONTENT_CHARS:
                content = content[:MAX_CONTENT_CHARS].rstrip() + " …"
            line = f"- [{doc}] ({src_type})"
            if src:
                line += f" src: {src}"
            line += f"\n  {content}"
            lines.append(line)
        return ToolResult(success=True, output="\n".join(lines))
    except Exception as e:
        logger.exception("search_kb failed")
        return ToolResult(success=False, output="", error=f"search_kb: {str(e)[:200]}")


@register_tool("recall_memory")
async def recall_memory_handler(arguments: dict, tool_config: dict | None = None) -> ToolResult:
    """Semantic search over the tenant's memory_entries (user-saved facts).
    Pinned items already live in the system prompt; use this to look up
    everything else by topic. Returns short list with id+content+type."""
    import uuid as _uuid
    from sqlalchemy import select, text as sa_text
    from app.core.database import async_session
    from app.core.config import settings as app_settings
    from app.models.tenant_shell_config import TenantShellConfig
    from app.providers.factory import get_provider

    ctx = (tool_config or {}).get("_context") or {}
    tenant_id_s = ctx.get("tenant_id")
    chat_id_s = ctx.get("chat_id")
    if not tenant_id_s:
        return ToolResult(success=False, output="", error="recall_memory: tenant context missing")

    query = (arguments.get("query") or "").strip()
    if not query:
        return ToolResult(success=False, output="", error="recall_memory: 'query' is required")
    limit = max(1, min(int(arguments.get("limit") or 5), 20))
    scope = (arguments.get("scope") or "chat").strip()
    memory_type = (arguments.get("memory_type") or "").strip().lower() or None

    try:
        async with async_session() as db:
            cfg = (await db.execute(
                select(TenantShellConfig).where(TenantShellConfig.tenant_id == _uuid.UUID(tenant_id_s))
            )).scalar_one_or_none()
            if not cfg or not cfg.embedding_model_name:
                return ToolResult(success=False, output="", error="recall_memory: no embedding_model_name configured")
            # cross-chat scope guarded by the same flag as recall_chat
            if scope == "tenant" and not getattr(cfg, "recall_cross_chat_enabled", False):
                scope = "chat"

            provider = get_provider("ollama", app_settings.OLLAMA_BASE_URL or "http://localhost:11434")
            vectors = await provider.embed(query, cfg.embedding_model_name)
            if not vectors:
                return ToolResult(success=False, output="", error="recall_memory: embedding failed")
            qvec_str = "[" + ",".join(f"{float(x):.6f}" for x in vectors[0]) + "]"

            params: dict = {"tid": _uuid.UUID(tenant_id_s), "qvec": qvec_str, "limit": limit}
            where_clauses = [
                "tenant_id = :tid",
                "deleted_at IS NULL",
                "embedding IS NOT NULL",
                # Pinned entries are already in the system prompt — no point
                # returning them here (duplicate context + token bloat).
                "is_pinned = false",
            ]
            if scope == "chat" and chat_id_s:
                params["cid"] = _uuid.UUID(chat_id_s)
                # chat-scope: items scoped to this chat OR tenant-wide (chat_id IS NULL)
                where_clauses.append("(chat_id = :cid OR chat_id IS NULL)")
            if memory_type:
                params["mtype"] = memory_type
                where_clauses.append("lower(memory_type) = :mtype")

            where_sql = " AND ".join(where_clauses)
            sql = sa_text(f"""
                SELECT
                    id::text AS id,
                    memory_type,
                    content,
                    chat_id::text AS chat_id,
                    created_at,
                    1 - (embedding <=> CAST(:qvec AS vector)) AS similarity
                FROM memory_entries
                WHERE {where_sql}
                ORDER BY embedding <=> CAST(:qvec AS vector)
                LIMIT :limit
            """)
            rows = (await db.execute(sql, params)).fetchall()

        if not rows:
            return ToolResult(success=True, output=f"(в памяти ничего не найдено: query={query!r}, scope={scope})")
        # Per-row content cap — long blobs are abusive in a tool response.
        # Caller can re-query with a tighter query or open the entry by id.
        MAX_CONTENT_CHARS = 300
        lines = [f"Найдено {len(rows)} записей в памяти (scope={scope}, без pinned):"]
        for r in rows:
            sim = f"sim={r.similarity:.2f}" if r.similarity is not None else "sim=—"
            scope_tag = "tenant" if r.chat_id is None else "chat"
            ts = r.created_at.strftime("%Y-%m-%d") if r.created_at else "—"
            content = (r.content or "").strip()
            if len(content) > MAX_CONTENT_CHARS:
                content = content[:MAX_CONTENT_CHARS].rstrip() + " …"
            lines.append(
                f"- [{r.id}] [{r.memory_type}|{scope_tag}] {sim} {ts}\n"
                f"  {content}"
            )
        return ToolResult(success=True, output="\n".join(lines))
    except Exception as e:
        logger.exception("recall_memory failed")
        return ToolResult(success=False, output="", error=f"recall_memory: {str(e)[:200]}")


# get_current_time was removed: the current date is now a system-prompt
# constant (HARDCODED-0 in pipeline.py) — there's no reason to make the
# model spend a tool round-trip on a deterministic value it could read
# directly. The model never needs the precise second resolution that the
# tool provided.


@register_tool("plan")
async def plan_handler(arguments: dict, tool_config: dict | None = None) -> ToolResult:
    """Register a multi-step plan as a `plan` artifact. No execution — just
    records the plan in DB and lets the model proceed step-by-step with normal
    tool calls. The artifact shows up in UI as a checklist; the model's own
    follow-up turns implicitly check items off."""
    import uuid as _uuid
    from datetime import datetime, timezone
    from app.core.database import async_session
    from app.models.artifact import Artifact

    ctx = (tool_config or {}).get("_context") or {}
    tenant_id_s = ctx.get("tenant_id")
    chat_id_s = ctx.get("chat_id")
    if not tenant_id_s or not chat_id_s:
        return ToolResult(success=False, output="", error="plan: tenant/chat context missing")

    steps = arguments.get("steps") or []
    if not isinstance(steps, list) or not steps:
        return ToolResult(success=False, output="", error="plan: 'steps' must be a non-empty list of strings")
    clean_steps = [str(s).strip() for s in steps if str(s).strip()]
    if len(clean_steps) < 2:
        return ToolResult(success=False, output="", error="plan: нужно минимум 2 шага. Для одношагового запроса plan не нужен — звать tool напрямую.")
    if len(clean_steps) > 8:
        clean_steps = clean_steps[:8]
    rationale = (arguments.get("rationale") or "").strip()[:500]

    # Markdown body as checklist — visible artifact for the user; the model
    # also reads it back via auto-grounding on subsequent turns if needed.
    lines = ["**План:**"]
    for i, s in enumerate(clean_steps, 1):
        lines.append(f"- [ ] {i}. {s}")
    if rationale:
        lines.append("")
        lines.append(f"_Обоснование: {rationale}_")
    content = "\n".join(lines)
    label = f"План: {clean_steps[0][:80]}{'...' if len(clean_steps) > 1 else ''}"

    try:
        async with async_session() as db:
            art = Artifact(
                tenant_id=_uuid.UUID(tenant_id_s),
                chat_id=_uuid.UUID(chat_id_s),
                source_message_id=_uuid.UUID(str(ctx.get("user_message_id"))) if ctx.get("user_message_id") else None,
                kind="plan",
                label=label[:500],
                lang=None,
                content=content,
                tokens_estimate=max(1, len(content) // 4),
                version=1,
                parent_artifact_id=None,
                last_referenced_at=datetime.now(timezone.utc),
            )
            db.add(art)
            await db.commit()
            await db.refresh(art)
            aid = art.id
    except Exception as e:
        return ToolResult(success=False, output="", error=f"plan: failed to save artifact: {e}")

    output = (
        f"План записан ({len(clean_steps)} шагов, artifact_id={aid}).\n\n"
        + content
        + "\n\nТеперь последовательно выполняй шаги через нужные tools. "
        "Перед финальным ответом отметь сделанное одним вызовом "
        "plan_update(done=[...]). В финальном ответе кратко сверь "
        "результаты с пунктами плана."
    )
    return ToolResult(success=True, output=output)


@register_tool("plan_update")
async def plan_update_handler(arguments: dict, tool_config: dict | None = None) -> ToolResult:
    """Mark steps of the chat's latest `plan` artifact as done/failed. Rewrites
    the checklist in place (version bump) so later turns reground a plan with
    real progress — otherwise the checkboxes stay empty forever and the model
    can't tell what's already been executed."""
    import re as _re
    import uuid as _uuid
    from datetime import datetime, timezone
    from sqlalchemy import select
    from app.core.database import async_session
    from app.models.artifact import Artifact

    ctx = (tool_config or {}).get("_context") or {}
    tenant_id_s = ctx.get("tenant_id")
    chat_id_s = ctx.get("chat_id")
    if not tenant_id_s or not chat_id_s:
        return ToolResult(success=False, output="", error="plan_update: tenant/chat context missing")

    try:
        done = {int(x) for x in (arguments.get("done") or [])}
        failed = {int(x) for x in (arguments.get("failed") or [])}
    except (TypeError, ValueError):
        return ToolResult(success=False, output="", error="plan_update: 'done'/'failed' — списки номеров шагов (integer)")
    if not done and not failed:
        return ToolResult(success=False, output="", error="plan_update: укажи хотя бы один шаг в 'done' или 'failed'")

    try:
        async with async_session() as db:
            art = (await db.execute(
                select(Artifact).where(
                    Artifact.tenant_id == _uuid.UUID(tenant_id_s),
                    Artifact.chat_id == _uuid.UUID(chat_id_s),
                    Artifact.kind == "plan",
                    Artifact.deleted_at.is_(None),
                ).order_by(Artifact.created_at.desc()).limit(1)
            )).scalar_one_or_none()
            if not art:
                return ToolResult(success=False, output="", error="plan_update: в этом чате нет плана (сначала вызови plan)")

            def _mark(line: str) -> str:
                m = _re.match(r"^- \[(?: |x|✗)\] (\d+)\.", line)
                if not m:
                    return line
                num = int(m.group(1))
                if num in done:
                    return "- [x]" + line[5:]
                if num in failed:
                    return "- [✗]" + line[5:]
                return line

            new_content = "\n".join(_mark(l) for l in (art.content or "").split("\n"))
            art.content = new_content
            art.version = (art.version or 1) + 1
            art.last_referenced_at = datetime.now(timezone.utc)
            await db.commit()
    except Exception as e:
        return ToolResult(success=False, output="", error=f"plan_update: {e}")

    return ToolResult(success=True, output="Прогресс отмечен.\n\n" + new_content)


@register_tool("describe_tool")
async def describe_tool_handler(arguments: dict, tool_config: dict | None = None) -> ToolResult:
    """Return the full schema of a tool that's listed in the compact catalog
    but isn't yet in the model's tools=[...] payload. Pipeline keeps the
    full allow-set in `_context.full_tool_catalog` (set at tool-selection
    time) so we don't have to re-query the DB here."""
    import json as _json
    name = (arguments.get("name") or "").strip()
    if not name:
        return ToolResult(success=False, output="", error="describe_tool: 'name' is required")

    ctx = (tool_config or {}).get("_context") or {}
    catalog = ctx.get("full_tool_catalog") if isinstance(ctx, dict) else None
    if not isinstance(catalog, dict):
        return ToolResult(success=False, output="", error="describe_tool: catalog not available in this round")

    entry = catalog.get(name)
    if not entry:
        sample = ", ".join(sorted(catalog.keys())[:15])
        return ToolResult(
            success=False, output="",
            error=f"describe_tool: tool '{name}' не найден. Доступные: {sample}{'...' if len(catalog) > 15 else ''}",
        )

    fn = entry.get("function") or {}
    desc = fn.get("description") or ""
    params = fn.get("parameters") or {}
    output = (
        f"Tool: {name}\n"
        f"Description:\n{desc}\n\n"
        f"Parameters schema:\n{_json.dumps(params, ensure_ascii=False, indent=2)}\n\n"
        "Вызови tool по имени с подходящими аргументами; пайплайн добавит "
        "его схему в payload на следующих раундах автоматически."
    )
    return ToolResult(success=True, output=output)
