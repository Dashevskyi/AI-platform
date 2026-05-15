"""add api_key_id to llm request logs

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-05-02 18:30:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "d0e1f2a3b4c5"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "llm_request_logs",
        sa.Column("api_key_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(op.f("ix_llm_request_logs_api_key_id"), "llm_request_logs", ["api_key_id"], unique=False)
    op.create_foreign_key(
        "fk_llm_request_logs_api_key_id_tenant_api_keys",
        "llm_request_logs",
        "tenant_api_keys",
        ["api_key_id"],
        ["id"],
    )
    op.execute(
        """
        UPDATE llm_request_logs AS l
        SET api_key_id = c.api_key_id
        FROM chats AS c
        WHERE l.chat_id = c.id
          AND l.api_key_id IS NULL
        """
    )


def downgrade() -> None:
    op.drop_constraint("fk_llm_request_logs_api_key_id_tenant_api_keys", "llm_request_logs", type_="foreignkey")
    op.drop_index(op.f("ix_llm_request_logs_api_key_id"), table_name="llm_request_logs")
    op.drop_column("llm_request_logs", "api_key_id")
