"""paper map: create paper_references

One OpenAlex identity per PDF document asset: id, DOI, year, citation
count, the short ids of the works it references, and a lookup status
(pending | matched | not_found).

Revision ID: c3d4e5f6a7b9
Revises: b2c3d4e5f6a8
Create Date: 2026-09-22 12:10:00.000000+00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c3d4e5f6a7b9"
down_revision: Union[str, None] = "b2c3d4e5f6a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "paper_references",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("openalex_id", sa.String(32), nullable=True),
        sa.Column("doi", sa.String(255), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("publication_year", sa.Integer(), nullable=True),
        sa.Column("cited_by_count", sa.Integer(), nullable=True),
        sa.Column("referenced_works", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("openalex_status", sa.String(16), nullable=False, server_default="pending"),
        sa.PrimaryKeyConstraint("id", name="pk_paper_references"),
        sa.ForeignKeyConstraint(
            ["asset_id"], ["assets.id"], name="fk_paper_references_asset_id_assets", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_paper_references_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("asset_id", name="uq_paper_references_asset_id"),
    )
    op.create_index("ix_paper_references_project_id", "paper_references", ["project_id"])
    op.create_index("ix_paper_references_openalex_id", "paper_references", ["openalex_id"])


def downgrade() -> None:
    op.drop_index("ix_paper_references_openalex_id", table_name="paper_references")
    op.drop_index("ix_paper_references_project_id", table_name="paper_references")
    op.drop_table("paper_references")
