"""scope message idempotency to tenant and chat

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-04-29 14:55:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index(op.f("ix_messages_idempotency_key"), table_name="messages")
    op.create_index(
        "uq_messages_tenant_chat_idempotency",
        "messages",
        ["tenant_id", "chat_id", "idempotency_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_messages_tenant_chat_idempotency", table_name="messages")
    op.create_index(op.f("ix_messages_idempotency_key"), "messages", ["idempotency_key"], unique=True)
