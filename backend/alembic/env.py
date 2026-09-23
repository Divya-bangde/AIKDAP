"""Alembic migration environment.

Runs migrations against the application's async SQLAlchemy engine,
sourcing the database URL from the application `Settings` object rather
than a hardcoded value in `alembic.ini`, and targeting the shared
declarative `Base.metadata` for autogeneration.
"""

import asyncio
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import settings
from app.database.base import Base

# Model modules must be imported here so their tables register on
# `Base.metadata` before autogenerate inspects it. Add new modules'
# model imports alongside this one as they are implemented.
from app.modules.assets.models import Asset  # noqa: F401
from app.modules.auth.models import User  # noqa: F401
from app.modules.execution.models import ExecutionAttempt, ExecutionJob  # noqa: F401
from app.modules.knowledge_base.models import ChunkPosition, KnowledgeChunk  # noqa: F401
from app.modules.papers.models import PaperReference  # noqa: F401
from app.modules.projects.models import Project  # noqa: F401
from app.modules.research.models import (  # noqa: F401
    AgentMessage,
    ResearchRun,
    ResearchStep,
)
from app.modules.tasks.models import Task  # noqa: F401

if sys.platform == "win32":
    # psycopg's async driver requires the selector event loop; asyncio's
    # default ProactorEventLoop on Windows cannot drive it.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit migration SQL without opening a live database connection."""
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Configure the migration context against a live sync connection."""
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations against the database using the async engine."""
    connectable: AsyncEngine = create_async_engine(
        settings.database_url,
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
