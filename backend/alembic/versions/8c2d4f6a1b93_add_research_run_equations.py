"""add research run equations

One nullable, additive JSONB column on `research_runs` (existing rows
need no backfill): the equations a synthesis answer relies on, each
validated by `schemas.Equation` and carrying its backend-sampled curve
(see `planner.equations`).

Revision ID: 8c2d4f6a1b93
Revises: 5f3c9e1a7b24
Create Date: 2026-09-12 12:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '8c2d4f6a1b93'
down_revision: Union[str, None] = '5f3c9e1a7b24'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'research_runs',
        sa.Column('equations', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('research_runs', 'equations')
