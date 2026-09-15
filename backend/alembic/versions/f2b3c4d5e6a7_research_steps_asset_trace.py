"""research steps can trace a report asset

Hardening item 1 for Milestone 10 step 4: report runs persist their
trace into the existing `research_steps` table instead of a second
step table (spec section 4: reports reuse `tracking.instrument` "for the
same trace, timeline, and failure policy"). A step now belongs to
exactly one owner -- a research run (`run_id`) or a report asset
(`asset_id`) -- enforced by a check constraint so a step can never be
orphaned or double-owned.

Revision ID: f2b3c4d5e6a7
Revises: e1a2b3c4d5f6
Create Date: 2026-09-15 12:00:00.000000+00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f2b3c4d5e6a7"
down_revision: Union[str, None] = "e1a2b3c4d5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "research_steps",
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_research_steps_asset_id_assets"),
        "research_steps",
        "assets",
        ["asset_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(op.f("ix_research_steps_asset_id"), "research_steps", ["asset_id"])
    # Which generation attempt of a report a step belongs to (a retry is
    # attempt 2, ...). Existing research-run steps get 1.
    op.add_column(
        "research_steps",
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
    )
    op.alter_column(
        "research_steps", "run_id", existing_type=postgresql.UUID(as_uuid=True), nullable=True
    )
    op.create_check_constraint(
        op.f("ck_research_steps_exactly_one_owner"),
        "research_steps",
        "num_nonnulls(run_id, asset_id) = 1",
    )


def downgrade() -> None:
    # Report-owned steps cannot survive `run_id` becoming NOT NULL again.
    op.execute("DELETE FROM research_steps WHERE run_id IS NULL")
    op.drop_constraint(op.f("ck_research_steps_exactly_one_owner"), "research_steps", type_="check")
    op.alter_column(
        "research_steps", "run_id", existing_type=postgresql.UUID(as_uuid=True), nullable=False
    )
    op.drop_column("research_steps", "attempt")
    op.drop_index(op.f("ix_research_steps_asset_id"), table_name="research_steps")
    op.drop_constraint(op.f("fk_research_steps_asset_id_assets"), "research_steps", type_="foreignkey")
    op.drop_column("research_steps", "asset_id")
