"""click-to-source: create chunk_positions

Where each PDF chunk's text sits on its page: line rectangles in PDF
points (top-left origin) plus a match quality. One row per chunk,
cascading with the chunk and with its document.

Revision ID: b2c3d4e5f6a8
Revises: a1b2c3d4e5f7
Create Date: 2026-09-22 12:05:00.000000+00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b2c3d4e5f6a8"
down_revision: Union[str, None] = "a1b2c3d4e5f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chunk_positions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("chunk_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column(
            "spans",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("match_quality", sa.String(10), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_chunk_positions"),
        sa.ForeignKeyConstraint(
            ["chunk_id"],
            ["knowledge_chunks.id"],
            name="fk_chunk_positions_chunk_id_knowledge_chunks",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["assets.id"],
            name="fk_chunk_positions_document_id_assets",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("chunk_id", name="uq_chunk_positions_chunk_id"),
    )
    op.create_index("ix_chunk_positions_document_id", "chunk_positions", ["document_id"])


def downgrade() -> None:
    op.drop_index("ix_chunk_positions_document_id", table_name="chunk_positions")
    op.drop_table("chunk_positions")
