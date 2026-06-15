"""Read-only schema introspection + safe dry-run SQL over a tenant data source.

Foundation for the conversational tool-builder agent (and a smarter Tier 0 /
tool test bench): lets an admin — or an agent acting on their behalf — SEE the
real tables/columns of a SQL data source and validate a SELECT before a tool is
created, instead of guessing column names.

STRICTLY read-only: introspection hits INFORMATION_SCHEMA; dry-run accepts a
single SELECT/WITH statement only (no DDL/DML, no multiple statements) and caps
the row count. Reuses the data-source connection helpers from tools.executor.
"""
import logging
import re

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import require_role, require_tenant_access, require_permission

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin/tenants/{tenant_id}/data-sources/{data_source_id}",
    tags=["admin-data-introspect"],
    dependencies=[
        Depends(require_role("superadmin", "tenant_admin")),
        Depends(require_tenant_access),
        Depends(require_permission("data_sources")),
    ],
)

# System schemas to hide from the table listing (noise for tool-building).
_SYSTEM_SCHEMAS = {
    "information_schema", "performance_schema", "mysql", "sys",
    "pg_catalog", "pg_toast",
}

# A dry-run statement must be a single read query. Block anything that could
# write or run multiple statements.
_FORBIDDEN = re.compile(
    r"(?is)\b(insert|update|delete|drop|alter|create|truncate|replace|grant|revoke|"
    r"merge|call|do|set|lock|copy|into\s+outfile|load\s+data)\b"
)


async def _db_url_for(data_source_id: str) -> tuple[str, str]:
    """Resolve a data source to (db_url, kind). Raises 400 for non-DB sources."""
    from app.services.tools.executor import _load_tenant_data_source, _build_db_url_from_data_source
    try:
        ds = await _load_tenant_data_source(data_source_id)
    except Exception as e:
        raise HTTPException(404, f"Источник данных недоступен: {e}")
    kind = str(ds.get("kind") or "").lower()
    if kind not in ("mysql", "mariadb", "postgresql"):
        raise HTTPException(400, f"Интроспекция доступна только для SQL-источников (kind={kind})")
    try:
        return _build_db_url_from_data_source(ds), kind
    except Exception as e:
        raise HTTPException(400, f"Не удалось собрать подключение: {e}")


async def _run(db_url: str, sql: str, params: dict) -> list[dict]:
    from app.services.tools.executor import _fetch_sql_rows
    return await _fetch_sql_rows(db_url, sql, params)


@router.get("/tables")
async def list_tables(tenant_id: uuid.UUID, data_source_id: str) -> dict:
    """List base tables (schema-qualified) in the data source — for picking the
    base table when building a tool."""
    db_url, _kind = await _db_url_for(data_source_id)
    sql = (
        "SELECT table_schema, table_name "
        "FROM information_schema.tables "
        "WHERE table_type = 'BASE TABLE' "
        "ORDER BY table_schema, table_name"
    )
    try:
        rows = await _run(db_url, sql, {})
    except Exception as e:
        raise HTTPException(502, f"Ошибка интроспекции: {str(e)[:300]}")
    tables = [
        {
            "schema": r.get("table_schema") or r.get("TABLE_SCHEMA"),
            "name": r.get("table_name") or r.get("TABLE_NAME"),
        }
        for r in rows
        if (r.get("table_schema") or r.get("TABLE_SCHEMA")) not in _SYSTEM_SCHEMAS
    ]
    return {"tables": tables, "count": len(tables)}


@router.get("/columns")
async def list_columns(tenant_id: uuid.UUID, data_source_id: str, table: str) -> dict:
    """Columns of a table (accepts `schema.table` or bare `table`) — name, type,
    nullability, key — so a tool's select/search/filter columns use REAL names."""
    db_url, _kind = await _db_url_for(data_source_id)
    schema = None
    tbl = table.strip()
    if "." in tbl:
        schema, tbl = tbl.split(".", 1)
    where = "table_name = :t"
    params: dict = {"t": tbl}
    if schema:
        where += " AND table_schema = :s"
        params["s"] = schema
    sql = (
        "SELECT column_name, data_type, is_nullable, column_default, "
        "ordinal_position "
        f"FROM information_schema.columns WHERE {where} "
        "ORDER BY ordinal_position"
    )
    try:
        rows = await _run(db_url, sql, params)
    except Exception as e:
        raise HTTPException(502, f"Ошибка интроспекции: {str(e)[:300]}")
    cols = [
        {
            "name": r.get("column_name") or r.get("COLUMN_NAME"),
            "type": r.get("data_type") or r.get("DATA_TYPE"),
            "nullable": (r.get("is_nullable") or r.get("IS_NULLABLE")) == "YES",
            "default": r.get("column_default") if "column_default" in r else r.get("COLUMN_DEFAULT"),
        }
        for r in rows
    ]
    if not cols:
        raise HTTPException(404, f"Таблица '{table}' не найдена или без колонок")
    return {"table": table, "columns": cols, "count": len(cols)}


class DryRunRequest(BaseModel):
    sql: str
    limit: int = 5


@router.post("/dry-run")
async def dry_run_sql(tenant_id: uuid.UUID, data_source_id: str, body: DryRunRequest) -> dict:
    """Validate + execute a single read-only SELECT, capped to `limit` rows.
    Returns the rows (or the SQL error) so a generated query can be verified
    BEFORE a tool is created. Rejects any write/DDL or multi-statement input."""
    raw = (body.sql or "").strip().rstrip(";").strip()
    if not raw:
        raise HTTPException(400, "Пустой SQL")
    if ";" in raw:
        raise HTTPException(400, "Разрешён только ОДИН оператор SELECT (без ';')")
    if not re.match(r"(?is)^\s*(select|with)\b", raw):
        raise HTTPException(400, "Разрешён только SELECT/WITH (read-only)")
    if _FORBIDDEN.search(raw):
        raise HTTPException(400, "Обнаружена запрещённая операция (только чтение разрешено)")

    limit = max(1, min(int(body.limit or 5), 50))
    db_url, _kind = await _db_url_for(data_source_id)
    # Wrap to enforce the row cap regardless of the query's own LIMIT.
    wrapped = f"SELECT * FROM (\n{raw}\n) AS _dryrun LIMIT {limit}"
    try:
        rows = await _run(db_url, wrapped, {})
        return {"ok": True, "rows": rows, "row_count": len(rows)}
    except Exception as e:
        # Surface the DB error verbatim — that's the whole point (fast diagnosis).
        return {"ok": False, "error": str(e)[:1500], "rows": [], "row_count": 0}
