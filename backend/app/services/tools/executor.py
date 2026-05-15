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

TOOL_TIMEOUT_SECONDS = 15
PING_BATCH_TIMEOUT_SECONDS = 60
MAX_TOOL_TIMEOUT_SECONDS = 120


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
        return result
    except asyncio.TimeoutError:
        return ToolResult(success=False, output="", error=f"Таймаут выполнения ({timeout}с)")
    except Exception as e:
        logger.exception(f"Tool execution error: {handler_name}")
        return ToolResult(success=False, output="", error=f"Ошибка: {str(e)[:300]}")


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
            select_column_exprs.append(
                f"{_quote_identifier_for_db(column_name, db_kind)} AS {alias_quote}{alias_name}{alias_quote}"
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
        query_terms = []
        for idx, column in enumerate(search_columns):
            param_name = f"query_{idx}"
            query_terms.append(_contains_expr(str(column), param_name, db_kind))
            params[param_name] = f"%{str(free_query).strip()}%"
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


@register_tool("fetch_api_data")
async def tool_fetch_api_data(arguments: dict, tool_config: dict | None = None) -> ToolResult:
    """
    Generic read-only HTTP API fetch tool.

    The admin config defines base_url, endpoint template and whitelisted params.
    """
    runtime = _extract_runtime_config(tool_config)

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
        async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True) as client:
            response = await client.request(method, url, **request_kwargs)
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


def _resolve_command(runtime: dict, arguments: dict) -> str:
    """Resolve whitelisted command template from arguments."""
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

    return template


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
        command = _resolve_command(runtime, arguments)
        creds = await _resolve_net_credentials(runtime)
    except ValueError as e:
        return ToolResult(success=False, output="", error=str(e))

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
        command = _resolve_command(runtime, arguments)
        creds = await _resolve_net_credentials(runtime)
    except ValueError as e:
        return ToolResult(success=False, output="", error=str(e))

    host = creds["host"]
    port = int(creds.get("port") or 23)
    username = creds.get("username") or ""
    password = creds.get("password") or ""
    timeout = min(int(runtime.get("timeout_seconds") or TELNET_EXEC_TIMEOUT), 30)

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
            sql = sa_text(f"""
                SELECT
                    m_user.id::text AS user_id,
                    m_user.resume_query,
                    m_user.created_at,
                    1 - (m_user.resume_embedding <=> CAST(:qvec AS vector)) AS similarity,
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
                  AND m_user.resume_embedding IS NOT NULL
                  {scope_clause}
                ORDER BY m_user.resume_embedding <=> CAST(:qvec AS vector)
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
    """Search past messages for artifacts (code, scripts, configs, instructions).
    Returns list of {message_id, kind, label, created_at, similarity?}.
    Call get_message(id) afterwards to fetch the full content."""
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
            # Cross-chat scope requires tenant opt-in (same flag as recall_chat).
            if scope == "tenant":
                cfg = (await db.execute(
                    select(TenantShellConfig).where(TenantShellConfig.tenant_id == _uuid.UUID(tenant_id_s))
                )).scalar_one_or_none()
                if not (cfg and getattr(cfg, "recall_cross_chat_enabled", False)):
                    scope = "chat"

            # Build embedding only if a query is provided AND embedding model configured.
            qvec_str: str | None = None
            if query:
                cfg = (await db.execute(
                    select(TenantShellConfig).where(TenantShellConfig.tenant_id == _uuid.UUID(tenant_id_s))
                )).scalar_one_or_none()
                if cfg and cfg.embedding_model_name:
                    provider = get_provider("ollama", app_settings.OLLAMA_BASE_URL or "http://localhost:11434")
                    vectors = await provider.embed(query, cfg.embedding_model_name)
                    if vectors:
                        qvec_str = "[" + ",".join(f"{float(x):.6f}" for x in vectors[0]) + "]"

            params: dict = {"tid": _uuid.UUID(tenant_id_s), "limit": limit}
            where_clauses = [
                "m.tenant_id = :tid",
                "m.role = 'assistant'",
                "m.artifacts IS NOT NULL",
                "jsonb_typeof(m.artifacts) = 'array'",
                "jsonb_array_length(m.artifacts) > 0",
            ]
            if scope == "chat":
                if not chat_id_s:
                    return ToolResult(success=False, output="", error="find_artifacts: chat context missing for scope=chat")
                params["cid"] = _uuid.UUID(chat_id_s)
                where_clauses.append("m.chat_id = :cid")
            if kind:
                params["kind"] = kind
                where_clauses.append(
                    "EXISTS (SELECT 1 FROM jsonb_array_elements(m.artifacts) elt "
                    "WHERE lower(elt->>'kind') = :kind)"
                )

            select_extra = ""
            order_clause = "ORDER BY m.created_at DESC"
            if qvec_str:
                params["qvec"] = qvec_str
                # Use resume_embedding similarity (lives on the matching USER row in same chat).
                select_extra = (
                    ", (SELECT 1 - (mu.resume_embedding <=> CAST(:qvec AS vector)) "
                    "   FROM messages mu "
                    "   WHERE mu.chat_id = m.chat_id AND mu.role = 'user' "
                    "         AND mu.resume_embedding IS NOT NULL "
                    "         AND mu.created_at <= m.created_at "
                    "   ORDER BY mu.created_at DESC LIMIT 1) AS similarity"
                )
                order_clause = "ORDER BY similarity DESC NULLS LAST, m.created_at DESC"

            where_sql = " AND ".join(where_clauses)
            sql = sa_text(f"""
                SELECT
                    m.id::text AS message_id,
                    m.chat_id::text AS chat_id,
                    m.created_at,
                    m.artifacts
                    {select_extra}
                FROM messages m
                WHERE {where_sql}
                {order_clause}
                LIMIT :limit
            """)
            rows = (await db.execute(sql, params)).fetchall()

        if not rows:
            cond = []
            if kind: cond.append(f"kind={kind}")
            if query: cond.append(f"query={query!r}")
            cond.append(f"scope={scope}")
            return ToolResult(success=True, output=f"(артефактов не найдено: {', '.join(cond)})")

        lines = [f"Найдено {len(rows)} (scope={scope}{', kind=' + kind if kind else ''}):"]
        for r in rows:
            ts = r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "—"
            sim_str = ""
            if qvec_str and getattr(r, "similarity", None) is not None:
                sim_str = f" sim={r.similarity:.2f}"
            arts = r.artifacts or []
            # Filter to the requested kind for display if specified
            shown = [a for a in arts if (not kind or (a.get("kind") or "").lower() == kind)] or arts
            for a in shown[:3]:  # max 3 artifacts per message in list view
                lines.append(
                    f"- [{r.message_id}] {ts}{sim_str}\n"
                    f"  📎 [{a.get('kind') or 'code'}] {a.get('label') or '(no label)'}"
                    + (f" (lang={a.get('lang')})" if a.get("lang") else "")
                )
        lines.append("\nДля получения полного текста — вызови get_message(id).")
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
