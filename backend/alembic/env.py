"""Alembic environment configuration.

Imports all model modules so their tables are registered with Base.metadata
before autogenerate runs.
"""

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Ensure the backend package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.config import settings
from app.db.session import Base

# Import EVERY model module so Base.metadata contains all tables before
# autogenerate diffs against it.  This list must stay in sync with the
# create_all bases in app/db/init.py and the side-effect imports in
# tests/conftest.py — a module missing here makes autogenerate think its
# tables were dropped.
import app.db.models  # noqa: F401
import app.db.models_auth  # noqa: F401
import app.db.models_project  # noqa: F401
import app.db.models_agent  # noqa: F401
import app.db.models_risk  # noqa: F401
import app.db.models_vulnerability  # noqa: F401
import app.db.models_confidence  # noqa: F401
import app.db.models_llm  # noqa: F401
import app.db.models_integrations  # noqa: F401

config = context.config

# Override sqlalchemy.url with the application's DATABASE_URL
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _include_object(object_, name, type_, reflected, compare_to):
    """Keep the pg_trgm GIN evidence indexes out of autogenerate.

    They are expression / ``gin_trgm_ops`` indexes that require the
    ``pg_trgm`` extension and have no portable model representation (adding
    them to ``__table_args__`` would break the metadata ``create_all`` used
    by the SQLite/PG test backends, which don't install the extension).
    They live solely in the hand-written ``*_hosts_dsl_trgm_indexes``
    migration; excluding them here stops a future ``--autogenerate`` from
    proposing to drop them.
    """
    if type_ == "index" and name and name.startswith("ix_trgm_"):
        return False
    return True


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL without a live connection)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=_include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=_include_object,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
