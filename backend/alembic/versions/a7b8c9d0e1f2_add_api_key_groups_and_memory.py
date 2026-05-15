"""add api key groups and memory

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-05-02 11:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "a7b8c9d0e1f2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenant_api_key_groups",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("memory_prompt", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_tenant_api_key_groups_tenant_id"), "tenant_api_key_groups", ["tenant_id"], unique=False)
    op.add_column("tenant_api_keys", sa.Column("group_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("tenant_api_keys", sa.Column("memory_prompt", sa.Text(), nullable=True))
    op.create_index(op.f("ix_tenant_api_keys_group_id"), "tenant_api_keys", ["group_id"], unique=False)
    op.create_foreign_key(
        "fk_tenant_api_keys_group_id",
        "tenant_api_keys",
        "tenant_api_key_groups",
        ["group_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_tenant_api_keys_group_id", "tenant_api_keys", type_="foreignkey")
    op.drop_index(op.f("ix_tenant_api_keys_group_id"), table_name="tenant_api_keys")
    op.drop_column("tenant_api_keys", "memory_prompt")
    op.drop_column("tenant_api_keys", "group_id")
    op.drop_index(op.f("ix_tenant_api_key_groups_tenant_id"), table_name="tenant_api_key_groups")
    op.drop_table("tenant_api_key_groups")
