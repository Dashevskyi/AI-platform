"""Row-Level Security on tenant-scoped tables (defense-in-depth isolation)

Each table gets a FORCE'd policy keyed on the per-transaction GUC
`app.current_tenant` (set by auth dependencies — see app/core/rls.py):

  * GUC set   → only that tenant's rows are visible/insertable.
  * GUC unset → bypass (superadmin, background jobs, scripts, alembic).

FORCE is required because the app connects as the *owner* role, and owners
bypass plain RLS. Fail-open-on-unset keeps every internal path working while
making a forgotten app-level tenant filter unable to leak across tenants.

admin_users is intentionally excluded: it's read during authentication, before
any tenant context exists, and superadmins legitimately have tenant_id = NULL.

revision = 'rls01'
down_revision = 'served01'
"""
from alembic import op


revision = 'rls01'
down_revision = 'served01'
branch_labels = None
depends_on = None

# All tables with a tenant_id column except admin_users (auth table).
TABLES = [
    "admin_audit_logs",
    "artifacts",
    "builtin_tool_overrides",
    "chats",
    "kb_chunks",
    "kb_documents",
    "llm_request_logs",
    "memory_entries",
    "message_attachment_chunks",
    "message_attachments",
    "messages",
    "tenant_api_key_groups",
    "tenant_api_keys",
    "tenant_custom_models",
    "tenant_data_sources",
    "tenant_model_configs",
    "tenant_shell_config_versions",
    "tenant_shell_configs",
    "tenant_tools",
]

# NULLIF(..., '') collapses both "unset" (NULL) and "cleared" (empty string,
# used for superadmin bypass) to NULL. The cast is applied to the NULLIF result,
# so '' is never cast to uuid — Postgres does NOT guarantee OR short-circuit and
# would otherwise raise "invalid input syntax for type uuid" on the bypass path.
_PREDICATE = (
    "NULLIF(current_setting('app.current_tenant', true), '') IS NULL "
    "OR tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid"
)


def upgrade() -> None:
    for t in TABLES:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {t} FOR ALL "
            f"USING ({_PREDICATE}) WITH CHECK ({_PREDICATE})"
        )


def downgrade() -> None:
    for t in TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t}")
        op.execute(f"ALTER TABLE {t} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY")
