"""add research run suggested papers

One nullable, additive JSONB column on `research_runs` (existing rows
need no backfill): the OpenAlex papers `paper_suggestion_node` found to
help close a gap in the answer. Each entry: openalex_id, title,
authors, year, cited_by_count, landing_url, oa_pdf_url (nullable),
relevance_note. Never used to ground `final_answer`.

Revision ID: c19e4b7d0a52
Revises: a41c7d2e9f05
Create Date: 2026-09-14 12:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c19e4b7d0a52'
down_revision: Union[str, None] = 'a41c7d2e9f05'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'research_runs',
        sa.Column('suggested_papers', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('research_runs', 'suggested_papers')
