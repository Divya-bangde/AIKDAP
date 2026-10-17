"""Verified embedding identities and resumable, staged rebuilds.

Legacy embeddings deliberately remain unverified (NULL identity).
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from pgvector.sqlalchemy import Vector

revision = "027_embedding_spaces"
down_revision = "026_cross_paper_artifacts"
branch_labels = None
depends_on = None


def audit_columns():
    return [sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)]


def upgrade():
    op.add_column("projects", sa.Column("active_embedding_space_id", sa.String(64), nullable=True))
    op.add_column("knowledge_chunks", sa.Column("embedding_space_id", sa.String(64), nullable=True))
    op.add_column("knowledge_chunks", sa.Column("embedding_space", pg.JSONB(), nullable=True))
    op.create_index("ix_knowledge_chunks_embedding_space_id", "knowledge_chunks", ["embedding_space_id"])
    op.create_table("embedding_rebuilds", *audit_columns(),
        sa.Column("project_id", pg.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("space_id", sa.String(64), nullable=False),
        sa.Column("space", pg.JSONB(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("completed", sa.Integer(), nullable=False),
        sa.Column("failed", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("claim_token", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("project_id", "space_id"))
    op.create_index("ix_embedding_rebuilds_project_id", "embedding_rebuilds", ["project_id"])
    op.create_table("embedding_rebuild_items", *audit_columns(),
        sa.Column("rebuild_id", pg.UUID(as_uuid=True), sa.ForeignKey("embedding_rebuilds.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chunk_id", pg.UUID(as_uuid=True), sa.ForeignKey("knowledge_chunks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("vector", Vector(1024), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.UniqueConstraint("rebuild_id", "chunk_id"))
    op.create_index("ix_embedding_rebuild_items_rebuild_id", "embedding_rebuild_items", ["rebuild_id"])
    op.create_index("ix_embedding_rebuild_items_chunk_id", "embedding_rebuild_items", ["chunk_id"])


def downgrade():
    op.drop_table("embedding_rebuild_items")
    op.drop_table("embedding_rebuilds")
    op.drop_column("knowledge_chunks", "embedding_space")
    op.drop_index("ix_knowledge_chunks_embedding_space_id", table_name="knowledge_chunks")
    op.drop_column("knowledge_chunks", "embedding_space_id")
    op.drop_column("projects", "active_embedding_space_id")
