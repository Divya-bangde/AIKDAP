"""add user persona and project persona override

One native enum shared by both columns. `users.persona` is non-null
with a server default of `researcher`, so existing rows are backfilled
by Postgres and existing users keep today's experience.
`projects.persona_override` is nullable: null inherits the owner's
persona.

Revision ID: a41c7d2e9f05
Revises: 8c2d4f6a1b93
Create Date: 2026-09-13 12:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'a41c7d2e9f05'
down_revision: Union[str, None] = '8c2d4f6a1b93'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

persona_enum = postgresql.ENUM('student', 'researcher', 'builder', name='persona_enum')


def upgrade() -> None:
    persona_enum.create(op.get_bind(), checkfirst=True)
    op.add_column(
        'users',
        sa.Column(
            'persona',
            postgresql.ENUM(name='persona_enum', create_type=False),
            nullable=False,
            server_default='researcher',
        ),
    )
    op.add_column(
        'projects',
        sa.Column(
            'persona_override',
            postgresql.ENUM(name='persona_enum', create_type=False),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column('projects', 'persona_override')
    op.drop_column('users', 'persona')
    persona_enum.drop(op.get_bind(), checkfirst=True)
