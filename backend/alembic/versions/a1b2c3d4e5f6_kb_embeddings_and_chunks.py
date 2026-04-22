"""KB embeddings and chunks

Revision ID: a1b2c3d4e5f6
Revises: cca41842b1df
Create Date: 2026-04-08 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB
from pgvector.sqlalchemy import Vector

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'cca41842b1df'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enable pgvector extension
    op.execute('CREATE EXTENSION IF NOT EXISTS vector')

    # Add new columns to kb_documents
    op.add_column('kb_documents', sa.Column('doc_type', sa.String(50), server_default='text', nullable=False))
    op.add_column('kb_documents', sa.Column('source_url', sa.String(2000), nullable=True))
    op.add_column('kb_documents', sa.Column('source_filename', sa.String(500), nullable=True))
    op.add_column('kb_documents', sa.Column('embedding_status', sa.String(20), server_default='pending', nullable=False))
    op.add_column('kb_documents', sa.Column('embedding_error', sa.Text(), nullable=True))
    op.add_column('kb_documents', sa.Column('chunks_count', sa.Integer(), server_default='0', nullable=False))

    # Add new columns to tenant_shell_configs
    op.add_column('tenant_shell_configs', sa.Column('embedding_model_name', sa.String(200), nullable=True))
    op.add_column('tenant_shell_configs', sa.Column('kb_max_chunks', sa.Integer(), server_default='10', nullable=False))

    # Create kb_chunks table
    op.create_table(
        'kb_chunks',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('document_id', UUID(as_uuid=True), sa.ForeignKey('kb_documents.id', ondelete='CASCADE'), nullable=False),
        sa.Column('tenant_id', UUID(as_uuid=True), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('chunk_index', sa.Integer(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('doc_title', sa.String(500), nullable=False),
        sa.Column('source_type', sa.String(50), server_default='manual'),
        sa.Column('source_url', sa.String(2000), nullable=True),
        sa.Column('embedding', Vector(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_kb_chunks_document_id', 'kb_chunks', ['document_id'])
    op.create_index('ix_kb_chunks_tenant_id', 'kb_chunks', ['tenant_id'])


def downgrade() -> None:
    op.drop_table('kb_chunks')
    op.drop_column('tenant_shell_configs', 'kb_max_chunks')
    op.drop_column('tenant_shell_configs', 'embedding_model_name')
    op.drop_column('kb_documents', 'chunks_count')
    op.drop_column('kb_documents', 'embedding_error')
    op.drop_column('kb_documents', 'embedding_status')
    op.drop_column('kb_documents', 'source_filename')
    op.drop_column('kb_documents', 'source_url')
    op.drop_column('kb_documents', 'doc_type')
