"""
Admin CRUD for tenant data sources.
"""
import json
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

from app.api.deps import require_role, require_tenant_access, require_permission
from app.core.database import get_db
from app.core.security import decrypt_value, encrypt_value, mask_secret
from app.models.admin_user import AdminUser
from app.models.tenant import Tenant
from app.models.tenant_data_source import TenantDataSource
from app.schemas.common import PaginatedResponse
from app.schemas.data_source import (
    TenantDataSourceCreate,
    TenantDataSourceResponse,
    TenantDataSourceUpdate,
)

router = APIRouter(
    prefix="/api/admin/tenants/{tenant_id}/data-sources",
    tags=["admin-data-sources"],
    dependencies=[Depends(require_role("superadmin", "tenant_admin")), Depends(require_tenant_access), Depends(require_permission("data_sources"))],
)


def _mask_secret_json(encrypted: str | None) -> dict | None:
    if not encrypted:
        return None
    try:
        data = json.loads(decrypt_value(encrypted))
    except Exception:
        return {"_error": "***INVALID_SECRET***"}
    result = {}
    for key, value in data.items():
        if value is None:
            result[key] = None
        else:
            result[key] = mask_secret(str(value))
    return result


def _data_source_to_response(ds: TenantDataSource) -> TenantDataSourceResponse:
    return TenantDataSourceResponse(
        id=str(ds.id),
        tenant_id=str(ds.tenant_id),
        name=ds.name,
        description=ds.description,
        kind=ds.kind,
        config_json=ds.config_json,
        secret_json_masked=_mask_secret_json(ds.secret_json_encrypted),
        is_active=ds.is_active,
        created_at=ds.created_at,
        updated_at=ds.updated_at,
    )


async def _verify_tenant(tenant_id: uuid.UUID, db: AsyncSession) -> Tenant:
    tenant = (
        await db.execute(select(Tenant).where(Tenant.id == tenant_id, Tenant.deleted_at.is_(None)))
    ).scalars().first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found.")
    return tenant


async def _get_data_source_or_404(tenant_id: uuid.UUID, data_source_id: uuid.UUID, db: AsyncSession) -> TenantDataSource:
    data_source = (
        await db.execute(
            select(TenantDataSource).where(
                TenantDataSource.id == data_source_id,
                TenantDataSource.tenant_id == tenant_id,
                TenantDataSource.deleted_at.is_(None),
            )
        )
    ).scalars().first()
    if not data_source:
        raise HTTPException(status_code=404, detail="Data source not found.")
    return data_source


@router.get("/", response_model=PaginatedResponse[TenantDataSourceResponse])
async def list_data_sources(
    tenant_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    await _verify_tenant(tenant_id, db)
    query = (
        select(TenantDataSource)
        .where(TenantDataSource.tenant_id == tenant_id, TenantDataSource.deleted_at.is_(None))
        .order_by(TenantDataSource.created_at.desc())
    )
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar()
    items = (await db.execute(query.offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return PaginatedResponse[TenantDataSourceResponse](
        items=[_data_source_to_response(item) for item in items],
        total_count=total,
        page=page,
        page_size=page_size,
    )


@router.post("/", response_model=TenantDataSourceResponse, status_code=status.HTTP_201_CREATED)
async def create_data_source(
    tenant_id: uuid.UUID,
    body: TenantDataSourceCreate,
    db: AsyncSession = Depends(get_db),
):
    await _verify_tenant(tenant_id, db)
    ds = TenantDataSource(
        tenant_id=tenant_id,
        name=body.name,
        description=body.description,
        kind=body.kind,
        config_json=body.config_json,
        secret_json_encrypted=encrypt_value(json.dumps(body.secret_json)) if body.secret_json is not None else None,
        is_active=body.is_active,
    )
    db.add(ds)
    await db.flush()
    await db.refresh(ds)
    return _data_source_to_response(ds)


@router.get("/{data_source_id}", response_model=TenantDataSourceResponse)
async def get_data_source(
    tenant_id: uuid.UUID,
    data_source_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    await _verify_tenant(tenant_id, db)
    ds = await _get_data_source_or_404(tenant_id, data_source_id, db)
    return _data_source_to_response(ds)


@router.patch("/{data_source_id}", response_model=TenantDataSourceResponse)
async def update_data_source(
    tenant_id: uuid.UUID,
    data_source_id: uuid.UUID,
    body: TenantDataSourceUpdate,
    db: AsyncSession = Depends(get_db),
):
    await _verify_tenant(tenant_id, db)
    ds = await _get_data_source_or_404(tenant_id, data_source_id, db)
    update_data = body.model_dump(exclude_unset=True)
    secret_json = update_data.pop("secret_json", None)
    for field, value in update_data.items():
        setattr(ds, field, value)
    if "secret_json" in body.model_fields_set:
        ds.secret_json_encrypted = encrypt_value(json.dumps(secret_json)) if secret_json is not None else None
    await db.flush()
    await db.refresh(ds)
    return _data_source_to_response(ds)


@router.delete("/{data_source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_data_source(
    tenant_id: uuid.UUID,
    data_source_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: AdminUser = Depends(require_role("superadmin", "tenant_admin")),
):
    await _verify_tenant(tenant_id, db)
    ds = await _get_data_source_or_404(tenant_id, data_source_id, db)
    ds.deleted_at = datetime.now(timezone.utc)
    ds.deleted_by = current_user.id
    await db.flush()


@router.get("/{data_source_id}/schema")
async def get_data_source_schema(
    tenant_id: uuid.UUID,
    data_source_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Return tables/views and their columns from the data source database."""
    await _verify_tenant(tenant_id, db)
    ds = await _get_data_source_or_404(tenant_id, data_source_id, db)

    if ds.kind not in ("postgresql", "mysql", "mariadb"):
        raise HTTPException(status_code=400, detail="Schema introspection is only supported for database sources")

    # Build connection URL
    from app.services.tools.executor import _build_db_url_from_data_source, _get_db_engine

    secret_json = {}
    if ds.secret_json_encrypted:
        secret_json = json.loads(decrypt_value(ds.secret_json_encrypted))

    ds_dict = {
        "kind": ds.kind,
        "config_json": ds.config_json or {},
        "secret_json": secret_json,
    }

    try:
        db_url = _build_db_url_from_data_source(ds_dict)
        engine = _get_db_engine(db_url)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Cannot connect to data source: {e}")

    try:
        async with engine.connect() as conn:
            if ds.kind == "postgresql":
                # Get tables and views
                tables_result = await conn.execute(text(
                    "SELECT table_schema, table_name, table_type "
                    "FROM information_schema.tables "
                    "WHERE table_schema NOT IN ('pg_catalog', 'information_schema') "
                    "ORDER BY table_schema, table_name"
                ))
                tables = [
                    {
                        "schema": r.table_schema,
                        "name": r.table_name,
                        "full_name": f"{r.table_schema}.{r.table_name}",
                        "type": "view" if r.table_type == "VIEW" else "table",
                    }
                    for r in tables_result
                ]

                columns_result = await conn.execute(text(
                    "SELECT table_schema, table_name, column_name, data_type, is_nullable "
                    "FROM information_schema.columns "
                    "WHERE table_schema NOT IN ('pg_catalog', 'information_schema') "
                    "ORDER BY table_schema, table_name, ordinal_position"
                ))
                columns = [
                    {
                        "table": f"{r.table_schema}.{r.table_name}",
                        "column": r.column_name,
                        "type": r.data_type,
                        "nullable": r.is_nullable == "YES",
                    }
                    for r in columns_result
                ]
            else:
                # MySQL/MariaDB
                db_name = (ds.config_json or {}).get("database", "")
                tables_result = await conn.execute(text(
                    "SELECT table_name, table_type "
                    "FROM information_schema.tables "
                    "WHERE table_schema = :db_name "
                    "ORDER BY table_name"
                ), {"db_name": db_name})
                tables = [
                    {
                        "schema": db_name,
                        "name": r.table_name,
                        "full_name": f"{db_name}.{r.table_name}" if db_name else r.table_name,
                        "type": "view" if r.table_type == "VIEW" else "table",
                    }
                    for r in tables_result
                ]

                columns_result = await conn.execute(text(
                    "SELECT table_name, column_name, data_type, is_nullable "
                    "FROM information_schema.columns "
                    "WHERE table_schema = :db_name "
                    "ORDER BY table_name, ordinal_position"
                ), {"db_name": db_name})
                columns = [
                    {
                        "table": f"{db_name}.{r.table_name}" if db_name else r.table_name,
                        "column": r.column_name,
                        "type": r.data_type,
                        "nullable": r.is_nullable == "YES",
                    }
                    for r in columns_result
                ]

        return {"tables": tables, "columns": columns}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Schema introspection failed for data source %s", data_source_id)
        raise HTTPException(status_code=500, detail=f"Failed to read schema: {e}")
