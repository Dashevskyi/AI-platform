"""add artifacts metadata column to messages

Revision ID: l2m3n4o5p6q7
Revises: k1l2m3n4o5p6
Create Date: 2026-05-14 00:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "l2m3n4o5p6q7"
down_revision: Union[str, None] = "k1l2m3n4o5p6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # List of artifact descriptors: [{"kind": "bash-script", "label": "...", "lang": "bash"}, ...]
    # The full content stays in messages.content — artifacts is metadata only.
    op.add_column("messages", sa.Column("artifacts", JSONB(), nullable=True))
    op.create_index(
        "ix_messages_artifacts_gin",
        "messages",
        ["artifacts"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_messages_artifacts_gin", table_name="messages")
    op.drop_column("messages", "artifacts")
