"""Semantic schema notes for a SQL data source — the MEANING layer.

Introspection (data_introspect.py) gives the tool-builder the STRUCTURE of a
data source (real table/column names, types). These notes give MEANING: what a
table holds, what a column is for, and FK-like relations between columns. The
builder agent reads this digest BEFORE it inspects structure, so it stops
guessing what columns mean and which joins to make.

Notes are seeded automatically from already-built tools — their
`result_columns` descriptions and `joins` already encode exactly this — and can
be extended by an admin (this API) or by the agent itself (save_schema_note).

This module also exposes plain helper functions (upsert_note, list_notes,
build_digest, seed_from_tools) that the tool-builder agent reuses directly.
"""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.api.deps import require_role, require_tenant_access, require_permission
from app.models.data_source_schema_note import DataSourceSchemaNote
from app.models.tenant_tool import TenantTool

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin/tenants/{tenant_id}/data-sources/{data_source_id}/schema-notes",
    tags=["admin-schema-notes"],
    dependencies=[
        Depends(require_role("superadmin", "tenant_admin")),
        Depends(require_tenant_access),
        Depends(require_permission("data_sources")),
    ],
)


# ─── normalization helpers ────────────────────────────────────────────────────

def _norm(value: str | None) -> str | None:
    """Trim and collapse empty → None so the (table, column) key is consistent."""
    if value is None:
        return None
    v = value.strip()
    return v or None


def _alias_table_map(runtime: dict) -> dict[str, str]:
    """Map SQL aliases → real tables from a search_records runtime config."""
    out: dict[str, str] = {}
    ta = _norm(runtime.get("table_alias"))
    base = _norm(runtime.get("table"))
    if ta and base:
        out[ta] = base
    for j in runtime.get("joins") or []:
        if isinstance(j, dict):
            a = _norm(j.get("alias"))
            t = _norm(j.get("table"))
            if a and t:
                out[a] = t
    return out


def _resolve_column(expr: str | None, amap: dict[str, str], base_table: str | None):
    """Resolve a column expression to (table, column) — only for plain
    `alias.col` or bare `col` refs. SQL expressions (functions, CONCAT, …) and
    unresolved aliases return None (we don't note computed columns)."""
    e = _norm(expr)
    if not e:
        return None
    # Reject anything that isn't a plain identifier reference.
    if any(ch in e for ch in "(), "):
        return None
    if "." in e:
        alias, col = e.split(".", 1)
        if "." in col:  # schema.table.col — not an alias ref we map
            return None
        table = amap.get(alias.strip())
        if not table:
            return None
        return (table, col.strip())
    if base_table:
        return (base_table, e)
    return None


# ─── DB helpers (reused by the agent) ─────────────────────────────────────────

async def upsert_note(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    data_source_id: uuid.UUID,
    table_name: str | None,
    column_name: str | None,
    description: str | None = None,
    references: str | None = None,
    source: str = "admin",
    fill_only: bool = False,
) -> DataSourceSchemaNote:
    """Create or update one note keyed by (data_source, table, column).

    `fill_only=True` (used by the seed) never clobbers a non-empty existing
    description/reference — it only fills blanks — so re-seeding is idempotent
    and human/agent edits win over auto-seed.
    """
    table_name = _norm(table_name)
    column_name = _norm(column_name)
    description = _norm(description)
    references = _norm(references)

    existing = (await db.execute(
        select(DataSourceSchemaNote).where(and_(
            DataSourceSchemaNote.data_source_id == data_source_id,
            DataSourceSchemaNote.table_name.is_(None) if table_name is None
            else DataSourceSchemaNote.table_name == table_name,
            DataSourceSchemaNote.column_name.is_(None) if column_name is None
            else DataSourceSchemaNote.column_name == column_name,
        ))
    )).scalar_one_or_none()

    if existing is None:
        note = DataSourceSchemaNote(
            tenant_id=tenant_id, data_source_id=data_source_id,
            table_name=table_name, column_name=column_name,
            description=description, references=references, source=source,
        )
        db.add(note)
        await db.flush()
        return note

    if fill_only:
        if description and not existing.description:
            existing.description = description
        if references and not existing.references:
            existing.references = references
    else:
        if description is not None:
            existing.description = description
        if references is not None:
            existing.references = references
        existing.source = source
    await db.flush()
    return existing


async def list_notes(
    db: AsyncSession, data_source_id: uuid.UUID,
) -> list[DataSourceSchemaNote]:
    return list((await db.execute(
        select(DataSourceSchemaNote)
        .where(DataSourceSchemaNote.data_source_id == data_source_id)
        .order_by(DataSourceSchemaNote.table_name, DataSourceSchemaNote.column_name)
    )).scalars().all())


def build_digest(notes: list[DataSourceSchemaNote]) -> str:
    """Render notes into a compact text digest for the agent."""
    if not notes:
        return "Справочник смысла для этого источника пуст."
    source_level = [n for n in notes if not n.table_name]
    by_table: dict[str, list[DataSourceSchemaNote]] = {}
    for n in notes:
        if n.table_name:
            by_table.setdefault(n.table_name, []).append(n)

    lines = ["СПРАВОЧНИК СМЫСЛА источника (понятийный слой; реальную СТРУКТУРУ смотри интроспекцией):"]
    for n in source_level:
        if n.description:
            lines.append(f"[Об источнике] {n.description}")
    for table in sorted(by_table):
        rows = by_table[table]
        table_desc = next((r.description for r in rows if not r.column_name and r.description), None)
        lines.append(f"Таблица {table}" + (f" — {table_desc}" if table_desc else ""))
        for r in sorted((x for x in rows if x.column_name), key=lambda x: x.column_name or ""):
            parts = []
            if r.description:
                parts.append(r.description)
            if r.references:
                parts.append(f"→ {r.references}")
            suffix = (" — " + "; ".join(parts)) if parts else ""
            lines.append(f"  • {r.column_name}{suffix}")
    return "\n".join(lines)


async def seed_from_tools(
    db: AsyncSession, tenant_id: uuid.UUID, data_source_id: uuid.UUID,
) -> dict:
    """Seed notes from the tenant's existing search_records tools bound to this
    data source: result_columns → column descriptions, joins → FK relations.
    Idempotent (fill_only) — never overwrites existing notes."""
    ds_id_str = str(data_source_id)
    tools = (await db.execute(
        select(TenantTool).where(and_(
            TenantTool.tenant_id == tenant_id,
            TenantTool.deleted_at.is_(None),
        ))
    )).scalars().all()

    seeded_cols = 0
    seeded_rels = 0
    for tool in tools:
        cfg = tool.config_json if isinstance(tool.config_json, dict) else {}
        runtime = None
        for key in ("x_backend_config", "backend_config", "runtime_config"):
            if isinstance(cfg.get(key), dict):
                runtime = cfg[key]
                break
        if not runtime or runtime.get("handler") != "search_records":
            continue
        if str(runtime.get("data_source_id") or "") != ds_id_str:
            continue

        amap = _alias_table_map(runtime)
        base = _norm(runtime.get("table"))

        for rc in runtime.get("result_columns") or []:
            if not isinstance(rc, dict):
                continue
            desc = _norm(rc.get("description"))
            if not desc:
                continue
            resolved = _resolve_column(rc.get("column"), amap, base)
            if not resolved:
                continue
            await upsert_note(
                db, tenant_id, data_source_id, resolved[0], resolved[1],
                description=desc, source="seed", fill_only=True,
            )
            seeded_cols += 1

        for j in runtime.get("joins") or []:
            if not isinstance(j, dict):
                continue
            left = _resolve_column(j.get("left_column"), amap, base)
            right = _resolve_column(j.get("right_column"), amap, base)
            if not left or not right:
                continue
            await upsert_note(
                db, tenant_id, data_source_id, left[0], left[1],
                references=f"{right[0]}.{right[1]}", source="seed", fill_only=True,
            )
            seeded_rels += 1

    return {"columns_seeded": seeded_cols, "relations_seeded": seeded_rels}


# ─── HTTP API ─────────────────────────────────────────────────────────────────

class SchemaNoteOut(BaseModel):
    id: str
    table_name: str | None
    column_name: str | None
    description: str | None
    references: str | None
    source: str


class SchemaNoteUpsert(BaseModel):
    table_name: str | None = None
    column_name: str | None = None
    description: str | None = None
    references: str | None = None


def _to_out(n: DataSourceSchemaNote) -> SchemaNoteOut:
    return SchemaNoteOut(
        id=str(n.id), table_name=n.table_name, column_name=n.column_name,
        description=n.description, references=n.references, source=n.source,
    )


@router.get("")
async def get_notes(
    tenant_id: uuid.UUID, data_source_id: uuid.UUID, db: AsyncSession = Depends(get_db),
) -> dict:
    notes = await list_notes(db, data_source_id)
    return {
        "notes": [_to_out(n) for n in notes],
        "digest": build_digest(notes),
        "count": len(notes),
    }


@router.put("")
async def put_note(
    tenant_id: uuid.UUID, data_source_id: uuid.UUID,
    body: SchemaNoteUpsert, db: AsyncSession = Depends(get_db),
) -> SchemaNoteOut:
    if not _norm(body.table_name) and not _norm(body.column_name) and not _norm(body.description):
        raise HTTPException(400, "Нужно описание (для источника) или таблица/колонка")
    if _norm(body.column_name) and not _norm(body.table_name):
        raise HTTPException(400, "Для заметки о колонке укажите таблицу")
    note = await upsert_note(
        db, tenant_id, data_source_id,
        body.table_name, body.column_name, body.description, body.references,
        source="admin", fill_only=False,
    )
    return _to_out(note)


@router.delete("/{note_id}")
async def delete_note(
    tenant_id: uuid.UUID, data_source_id: uuid.UUID, note_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict:
    note = (await db.execute(
        select(DataSourceSchemaNote).where(and_(
            DataSourceSchemaNote.id == note_id,
            DataSourceSchemaNote.data_source_id == data_source_id,
        ))
    )).scalar_one_or_none()
    if not note:
        raise HTTPException(404, "Заметка не найдена")
    await db.delete(note)
    await db.flush()
    return {"ok": True}


@router.post("/seed")
async def seed_notes(
    tenant_id: uuid.UUID, data_source_id: uuid.UUID, db: AsyncSession = Depends(get_db),
) -> dict:
    result = await seed_from_tools(db, tenant_id, data_source_id)
    notes = await list_notes(db, data_source_id)
    result["total"] = len(notes)
    return result
