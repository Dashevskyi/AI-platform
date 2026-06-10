"""Row-Level Security tenant context.

Defense-in-depth for tenant isolation. The app-level `where tenant_id=...`
filters remain the primary mechanism; RLS is the backstop that makes a *missed*
filter (e.g. a route that forgot its tenant guard) unable to leak across tenants.

Model — fail-open on absence:
  * context set   → Postgres policies expose only rows of that tenant.
  * context unset → bypass (superadmin, background jobs, scripts, migrations
    keep working unchanged). See alembic migration `rls01`.

The context is transaction-local (`set_config(..., is_local=true)`), so it is
automatically cleared on COMMIT/ROLLBACK and never leaks across pooled
connections / requests.
"""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_SET_TENANT_SQL = text("SELECT set_config('app.current_tenant', :tid, true)")
_CLEAR_TENANT_SQL = text("SELECT set_config('app.current_tenant', '', true)")


async def set_tenant_context(db: AsyncSession, tenant_id) -> None:
    """Scope this transaction's queries to `tenant_id` via RLS.

    Must run inside a transaction (SQLAlchemy opens one lazily on first query,
    and auth dependencies always SELECT before calling this). Passing a falsy
    tenant_id clears the context (→ bypass), used for superadmins.
    """
    if tenant_id:
        await db.execute(_SET_TENANT_SQL, {"tid": str(tenant_id)})
    else:
        await db.execute(_CLEAR_TENANT_SQL)
