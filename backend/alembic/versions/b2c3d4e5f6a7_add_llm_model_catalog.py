"""Add LLM model catalog: llm_models, tenant_custom_models, tenant_model_configs

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-04-23 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Global model catalog
    op.create_table(
        "llm_models",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("provider_type", sa.String(50), nullable=False),
        sa.Column("base_url", sa.String(500), nullable=True),
        sa.Column("api_key_enc", sa.Text, nullable=True),
        sa.Column("model_id", sa.String(200), nullable=False),
        sa.Column("tier", sa.String(20), nullable=False, server_default="medium"),
        sa.Column("supports_tools", sa.Boolean, server_default=sa.text("false"), nullable=False),
        sa.Column("supports_vision", sa.Boolean, server_default=sa.text("false"), nullable=False),
        sa.Column("max_context_tokens", sa.Integer, nullable=True),
        sa.Column("cost_per_1k_input", sa.Float, nullable=True),
        sa.Column("cost_per_1k_output", sa.Float, nullable=True),
        sa.Column("is_active", sa.Boolean, server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    # Tenant private models
    op.create_table(
        "tenant_custom_models",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("provider_type", sa.String(50), nullable=False),
        sa.Column("base_url", sa.String(500), nullable=True),
        sa.Column("api_key_enc", sa.Text, nullable=True),
        sa.Column("model_id", sa.String(200), nullable=False),
        sa.Column("tier", sa.String(20), nullable=False, server_default="medium"),
        sa.Column("supports_tools", sa.Boolean, server_default=sa.text("false"), nullable=False),
        sa.Column("supports_vision", sa.Boolean, server_default=sa.text("false"), nullable=False),
        sa.Column("max_context_tokens", sa.Integer, nullable=True),
        sa.Column("is_active", sa.Boolean, server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by", UUID(as_uuid=True), nullable=True),
    )

    # Tenant model config (selection: manual/auto)
    op.create_table(
        "tenant_model_configs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False, unique=True, index=True),
        sa.Column("mode", sa.String(20), nullable=False, server_default="manual"),
        sa.Column("manual_model_id", UUID(as_uuid=True), sa.ForeignKey("llm_models.id", ondelete="SET NULL"), nullable=True),
        sa.Column("manual_custom_model_id", UUID(as_uuid=True), sa.ForeignKey("tenant_custom_models.id", ondelete="SET NULL"), nullable=True),
        sa.Column("auto_light_model_id", UUID(as_uuid=True), sa.ForeignKey("llm_models.id", ondelete="SET NULL"), nullable=True),
        sa.Column("auto_heavy_model_id", UUID(as_uuid=True), sa.ForeignKey("llm_models.id", ondelete="SET NULL"), nullable=True),
        sa.Column("auto_light_custom_model_id", UUID(as_uuid=True), sa.ForeignKey("tenant_custom_models.id", ondelete="SET NULL"), nullable=True),
        sa.Column("auto_heavy_custom_model_id", UUID(as_uuid=True), sa.ForeignKey("tenant_custom_models.id", ondelete="SET NULL"), nullable=True),
        sa.Column("complexity_threshold", sa.Float, nullable=False, server_default=sa.text("0.5")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("tenant_model_configs")
    op.drop_table("tenant_custom_models")
    op.drop_table("llm_models")
