"""admin_users.token_version for JWT revocation

Each access token carries the user's token_version in a "ver" claim. Bumping
the column (logout / password change / compromise) invalidates every token
issued before the bump — turning otherwise-stateless JWTs into revocable ones.

revision = 'jwtver01'
down_revision = 'vecidx01'
"""
import sqlalchemy as sa
from alembic import op


revision = 'jwtver01'
down_revision = 'vecidx01'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'admin_users',
        sa.Column('token_version', sa.Integer(), nullable=False, server_default='0'),
    )


def downgrade() -> None:
    op.drop_column('admin_users', 'token_version')
