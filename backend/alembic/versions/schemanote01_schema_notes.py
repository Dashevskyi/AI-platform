"""schema notes: semantic layer over a data source's tables/columns

A per-data-source store of MEANING (what a table/column is for, FK-like
relations) that complements structural introspection. Read by the tool-builder
agent before it inspects structure; seeded from existing tools and extendable
by admin or the agent.

Revision ID: schemanote01
Revises: assistant01
Create Date: 2026-06-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "schemanote01"
down_revision: Union[str, None] = "assistant01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "data_source_schema_notes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("data_source_id", UUID(as_uuid=True), sa.ForeignKey("tenant_data_sources.id"), nullable=False),
        sa.Column("table_name", sa.String(255), nullable=True),
        sa.Column("column_name", sa.String(255), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("references", sa.String(512), nullable=True),
        sa.Column("source", sa.String(20), nullable=False, server_default="admin"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint(
            "data_source_id", "table_name", "column_name",
            name="uq_schema_note_ds_table_column",
        ),
    )
    op.create_index("ix_dssn_tenant_id", "data_source_schema_notes", ["tenant_id"])
    op.create_index("ix_dssn_data_source_id", "data_source_schema_notes", ["data_source_id"])


def downgrade() -> None:
    op.drop_index("ix_dssn_data_source_id", table_name="data_source_schema_notes")
    op.drop_index("ix_dssn_tenant_id", table_name="data_source_schema_notes")
    op.drop_table("data_source_schema_notes")
