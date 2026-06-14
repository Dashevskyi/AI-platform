"""assistant layer: assistants table + chat/api_key bindings

Introduces the Assistant layer between Tenant and the per-request config.
Each tenant gets one default assistant (overrides={}) so behaviour is
unchanged: an empty-override default assistant resolves to exactly the
tenant's TenantShellConfig.

Revision ID: assistant01
Revises: toollimitauto01
Create Date: 2026-06-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision: str = "assistant01"
down_revision: Union[str, None] = "toollimitauto01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "assistants",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False, server_default="Основной"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("overrides", JSONB(), nullable=False, server_default="{}"),
        sa.Column("allowed_tool_ids", JSONB(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_assistants_tenant_id", "assistants", ["tenant_id"])

    op.add_column("chats", sa.Column("assistant_id", UUID(as_uuid=True), sa.ForeignKey("assistants.id"), nullable=True))
    op.create_index("ix_chats_assistant_id", "chats", ["assistant_id"])
    op.add_column("tenant_api_keys", sa.Column("assistant_id", UUID(as_uuid=True), sa.ForeignKey("assistants.id"), nullable=True))
    op.create_index("ix_tenant_api_keys_assistant_id", "tenant_api_keys", ["assistant_id"])

    # Backfill: one default assistant per tenant (empty overrides = identical
    # to the tenant default config → zero behaviour change).
    op.execute(
        """
        INSERT INTO assistants (id, tenant_id, name, is_default, overrides, is_active, created_at, updated_at)
        SELECT gen_random_uuid(), t.id, 'Основной', true, '{}'::jsonb, true, now(), now()
        FROM tenants t
        """
    )


def downgrade() -> None:
    op.drop_index("ix_tenant_api_keys_assistant_id", table_name="tenant_api_keys")
    op.drop_column("tenant_api_keys", "assistant_id")
    op.drop_index("ix_chats_assistant_id", table_name="chats")
    op.drop_column("chats", "assistant_id")
    op.drop_index("ix_assistants_tenant_id", table_name="assistants")
    op.drop_table("assistants")
