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
# Importing the registry populates Base.metadata with EVERY model's tables
# before autogenerate diffs against it. A module missing here made autogenerate
# think its tables were dropped — which is exactly how attribution + tool_registry
# came to be proposed for deletion. The registry is the single list now.
from app.db.model_registry import Base  # noqa: F401

config = context.config

# Override sqlalchemy.url with the application's DATABASE_URL
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


# Indexes that exist only in the database, created by hand-written migrations
# because they cannot be expressed in portable model metadata: partial (WHERE)
# indexes, and expression / GIN / GiST indexes. Adding them to __table_args__
# would break the metadata create_all() the SQLite/PG test backends use (no
# pg_trgm, no inet_ops, etc.), so they live in migrations alone — and are
# excluded here so a future --autogenerate never proposes to drop them.
# (The pg_trgm evidence indexes are handled by the ix_trgm_ prefix rule below.)
_UNMODELABLE_INDEXES = frozenset({
    "idx_host_ip_inet",                              # ((ip_address)::inet) expression
    "idx_host_ip_inet_gist",                         # gist ((ip_address)::inet) inet_ops
    "ix_agent_api_calls_referenced_host_ids_gin",    # gin ((referenced_host_ids)::jsonb)
    "ix_agent_api_calls_referenced_target_ips_gin",  # gin ((referenced_target_ips)::jsonb)
    "ix_ingestion_jobs_processing_heartbeat",        # partial WHERE status='processing'
    "ix_ingestion_jobs_queued_created",              # partial WHERE status='queued'
    "ix_report_jobs_processing_heartbeat",           # partial WHERE status='processing'
    "ix_report_jobs_queued_created",                 # partial WHERE status='queued'
    "uq_api_key_agent_session_active",               # partial UNIQUE WHERE is_active (rebased off test_plan_id in the contract phase)
    "uq_exec_session_plan_active",                   # partial UNIQUE WHERE status='active'
})


def _include_object(object_, name, type_, reflected, compare_to):
    """Exclude indexes that have no faithful model representation.

    Three classes are ignored in BOTH directions (reflected DB + metadata) so
    autogenerate never proposes to add or drop them:

    * ``ix_trgm_*`` — the pg_trgm GIN evidence indexes (extension-dependent).
    * ``_UNMODELABLE_INDEXES`` — partial / expression / GIN / GiST indexes that
      live only in hand-written migrations.
    * a single-column index on a primary-key ``id`` column — a PK already has a
      unique btree, so ``ix_<table>_id`` is redundant. Older tables carry it
      (from the historical create_all), newer ones don't (migration-built);
      ignoring it stops that split from reading as drift and stops ``index=True``
      on a PK proposing a fresh redundant index.
    """
    if type_ == "index":
        if name and name.startswith("ix_trgm_"):
            return False
        if name in _UNMODELABLE_INDEXES:
            return False
        try:
            cols = list(object_.columns)
        except Exception:  # pragma: no cover - defensive
            cols = []
        if name and name.endswith("_id") and len(cols) == 1 and cols[0].name == "id":
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
