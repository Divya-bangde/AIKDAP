"""source mix bar: add source_mix to research runs and assets

Nullable JSONB `{kb, web, general, unit}` on `research_runs` and
`assets`. Null means "not computed" (saved before this existed and not
yet backfilled), which the UI treats as "hide the bar".

Revision ID: d4e5f6a7b8c0
Revises: c3d4e5f6a7b9
Create Date: 2026-09-22 12:15:00.000000+00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d4e5f6a7b8c0"
down_revision: Union[str, None] = "c3d4e5f6a7b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "research_runs",
        sa.Column("source_mix", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "assets", sa.Column("source_mix", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("assets", "source_mix")
    op.drop_column("research_runs", "source_mix")
