"""Versioned understanding cache and immutable cross-paper artifacts.

No legacy fabricated comparisons or unkeyed understandings are backfilled.
Downgrade removes saved comparisons and cache entries, leaving source assets intact.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "026_cross_paper_artifacts"
down_revision = "d4e5f6a7b8c0"
branch_labels = None
depends_on = None


def _audit_columns():
    return [
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    ]


def upgrade():
    op.create_table(
        "document_understanding_cache",
        *_audit_columns(),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("assets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cache_key", sa.String(64), nullable=False, unique=True),
        sa.Column("source_version", postgresql.JSONB(), nullable=False),
        sa.Column("understanding", postgresql.JSONB(), nullable=False),
    )
    op.create_index("ix_document_understanding_cache_asset_id", "document_understanding_cache", ["asset_id"])
    op.create_table(
        "cross_paper_artifacts",
        *_audit_columns(),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("primary_asset_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("assets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=False),
    )
    op.create_index("ix_cross_paper_artifacts_lookup", "cross_paper_artifacts", ["owner_id", "project_id", "primary_asset_id", "created_at", "id"])
    op.create_index("ix_research_runs_owner_created_id", "research_runs", ["owner_id", "created_at", "id"])


def downgrade():
    op.drop_index("ix_research_runs_owner_created_id", table_name="research_runs")
    op.drop_table("cross_paper_artifacts")
    op.drop_table("document_understanding_cache")
