"""add auto_size_threshold and use_complexity_classifier to tenant_model_configs

Size-based routing: estimate prompt tokens per round, route to heavy when
above threshold. Replaces classifier guessing.

Revision ID: ee05ff06aa07
Revises: dd04ee05ff06
Create Date: 2026-05-18 12:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "ee05ff06aa07"
down_revision: Union[str, None] = "dd04ee05ff06"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tenant_model_configs",
        sa.Column("auto_size_threshold", sa.Integer(), nullable=False, server_default="24000"),
    )
    op.add_column(
        "tenant_model_configs",
        sa.Column("use_complexity_classifier", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("tenant_model_configs", "use_complexity_classifier")
    op.drop_column("tenant_model_configs", "auto_size_threshold")
