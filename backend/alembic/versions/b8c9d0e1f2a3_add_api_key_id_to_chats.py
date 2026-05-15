"""add api_key_id to chats

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-05-02 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "b8c9d0e1f2a3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chats", sa.Column("api_key_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index(op.f("ix_chats_api_key_id"), "chats", ["api_key_id"], unique=False)
    op.create_foreign_key(
        "fk_chats_api_key_id_tenant_api_keys",
        "chats",
        "tenant_api_keys",
        ["api_key_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_chats_api_key_id_tenant_api_keys", "chats", type_="foreignkey")
    op.drop_index(op.f("ix_chats_api_key_id"), table_name="chats")
    op.drop_column("chats", "api_key_id")
