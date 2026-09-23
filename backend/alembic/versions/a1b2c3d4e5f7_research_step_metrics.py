"""workflow timeline: model, tokens and metadata on research steps

Adds the per-step model provider/name, token counts and a small JSONB
`metadata` object to `research_steps` (all nullable or defaulted, so
existing rows stay valid), plus an index on `(run_id, step_index)` for
reading a run's timeline in order.

Revision ID: a1b2c3d4e5f7
Revises: f2b3c4d5e6a7
Create Date: 2026-09-22 12:00:00.000000+00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a1b2c3d4e5f7"
down_revision: Union[str, None] = "f2b3c4d5e6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("research_steps", sa.Column("model_provider", sa.String(100), nullable=True))
    op.add_column("research_steps", sa.Column("model_name", sa.String(255), nullable=True))
    op.add_column("research_steps", sa.Column("input_tokens", sa.Integer(), nullable=True))
    op.add_column("research_steps", sa.Column("output_tokens", sa.Integer(), nullable=True))
    op.add_column(
        "research_steps",
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_index(
        "ix_research_steps_run_id_step_index", "research_steps", ["run_id", "step_index"]
    )


def downgrade() -> None:
    op.drop_index("ix_research_steps_run_id_step_index", table_name="research_steps")
    op.drop_column("research_steps", "metadata")
    op.drop_column("research_steps", "output_tokens")
    op.drop_column("research_steps", "input_tokens")
    op.drop_column("research_steps", "model_name")
    op.drop_column("research_steps", "model_provider")
