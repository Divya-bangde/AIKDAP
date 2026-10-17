"""Independent visualization intent, verification, and completion status.

No backfill: historical charts have unknown verification, never fabricated proof.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "028_visualization_outcome"
down_revision = "027_embedding_spaces"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("research_runs", sa.Column("visualization_outcome", postgresql.JSONB(), nullable=True))


def downgrade():
    op.drop_column("research_runs", "visualization_outcome")
