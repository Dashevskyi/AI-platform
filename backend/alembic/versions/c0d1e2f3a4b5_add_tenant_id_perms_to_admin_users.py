"""add tenant_id and permissions to admin_users

Revision ID: c0d1e2f3a4b5
Revises: b8c9d0e1f2a4
Create Date: 2026-05-06 22:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = "c0d1e2f3a4b5"
down_revision = "b8c9d0e1f2a4"
branch_labels = None
depends_on = None


SUPERADMIN_PERMS = [
    "tools",
    "data_sources",
    "keys",
    "model_config",
    "shell_config",
    "kb",
    "memory",
    "chats",
    "logs",
    "users",
]


def upgrade() -> None:
    op.add_column(
        "admin_users",
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_admin_users_tenant_id",
        "admin_users",
        "tenants",
        ["tenant_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_admin_users_tenant_id",
        "admin_users",
        ["tenant_id"],
    )
    op.add_column(
        "admin_users",
        sa.Column(
            "permissions",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )

    # Backfill: existing superadmins get all perms; tenant_admins keep empty
    # (must be reassigned explicitly by superadmin via UI/script).
    superadmin_perms_sql = sa.text(
        "UPDATE admin_users SET permissions = :perms WHERE role = 'superadmin'"
    )
    op.get_bind().execute(
        superadmin_perms_sql.bindparams(
            sa.bindparam("perms", value=SUPERADMIN_PERMS, type_=JSONB)
        )
    )


def downgrade() -> None:
    op.drop_index("ix_admin_users_tenant_id", table_name="admin_users")
    op.drop_constraint("fk_admin_users_tenant_id", "admin_users", type_="foreignkey")
    op.drop_column("admin_users", "tenant_id")
    op.drop_column("admin_users", "permissions")
