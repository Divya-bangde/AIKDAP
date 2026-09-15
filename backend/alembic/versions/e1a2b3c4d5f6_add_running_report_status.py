"""add running report status

Milestone 10 step 4. Adds `running` to the existing
`asset_processing_status_enum` Postgres enum so a report-generation
Celery job (see `app.workers.tasks.generate_report`) can record that it
started, reusing `Asset.processing_status`/`Asset.processing_error`
instead of adding new columns. Autogenerate does not detect enum member
changes (the same limitation `fae4f91a412b` already documents), so this
is hand-written, following that migration's exact pattern.

`ALTER TYPE ... ADD VALUE` is safe inside Alembic's normal transactional
migration on Postgres 12+ (this project runs 17) as long as the new
value is only added, never referenced, within the same transaction --
which this migration does not do.

Revision ID: e1a2b3c4d5f6
Revises: c19e4b7d0a52
Create Date: 2026-09-15 00:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e1a2b3c4d5f6'
down_revision: Union[str, None] = 'c19e4b7d0a52'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE asset_processing_status_enum ADD VALUE IF NOT EXISTS 'running'")


def downgrade() -> None:
    # Postgres has no `ALTER TYPE ... DROP VALUE` -- removing an enum
    # value requires rebuilding the type, which is unsafe to do
    # automatically without knowing whether any row still uses it.
    pass
