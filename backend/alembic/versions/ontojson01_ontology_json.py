"""structured ontology (ontology_json) on tenant_shell_configs

Source-of-truth structured ontology (sections incl. a decision graph). The
plain-text `ontology_prompt` the LLM reads is regenerated from this on save;
when `ontology_json` is null the old text is used as-is (backward compatible).

Revision ID: ontojson01
Revises: tune01
Create Date: 2026-06-25 01:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "ontojson01"
down_revision: Union[str, None] = "tune01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tenant_shell_configs",
        sa.Column("ontology_json", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenant_shell_configs", "ontology_json")
