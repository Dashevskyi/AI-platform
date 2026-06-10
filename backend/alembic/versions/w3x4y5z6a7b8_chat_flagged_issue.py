"""add flagged_issue + flagged_at to chats

Lets the user (himself, during the 100-chat data collection) mark a chat as
'problematic' with a free-form note. The note + timestamp lets the offline
analysis prioritise traces from chats the user already flagged as broken.

Revision ID: w3x4y5z6a7b8
Revises: v2w3x4y5z6a7
Create Date: 2026-05-15 23:30:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "w3x4y5z6a7b8"
down_revision: Union[str, None] = "v2w3x4y5z6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("chats", sa.Column("flagged_issue", sa.Text(), nullable=True))
    op.add_column("chats", sa.Column("flagged_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("chats", "flagged_at")
    op.drop_column("chats", "flagged_issue")
