"""agent_api_calls: nullable agent_id/project_id + error_class for pre-auth 5xx audit

Revision ID: f9e2d471a8c6
Revises: d8f1a4b9c205
Create Date: 2026-05-18 22:50:00.000000

Recon-agent feedback (id=2, source=reconnaissance) identified an audit
blindspot: requests that 5xx'd before the auth dependency completed
were entirely missing from ``agent_api_calls`` because the table
required NOT NULL agent_id + project_id (which the auth dep populates
on request.state).  Three POSTs to /agent/recon/sessions/1/environment
went unrecorded during recon session #1 — operator could only spot
them via nginx access logs.

The v2.44.2 hotfix surfaced these via a structured WARNING log line
(see agent_api_log_service.dispatch:323-339).  Logs cover the
operational need but aren't SQL-queryable — operators can't run
``SELECT ... WHERE error_class IS NOT NULL`` against the table.

This migration:

* Drops NOT NULL on ``agent_id`` and ``project_id``.  Both still
  carry FKs (CASCADE / CASCADE).  Cross-referencing intent: NULL
  means "the request reached the agent surface but never got an
  identity stamped on request.state" — usually a pre-auth crash;
  occasionally a misrouted request.

* Adds ``error_class`` (VARCHAR(64), indexed, nullable).  Populated
  by the global exception handler via request.state when an
  unhandled exception fires.  NULL for 2xx/4xx and for the (rare)
  5xx paths that bypass the handler.

* No data backfill — existing rows pre-date the gap and keep their
  NOT NULL-honored values.
"""
from alembic import op
import sqlalchemy as sa


revision = "f9e2d471a8c6"
down_revision = "d8f1a4b9c205"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "agent_api_calls",
        "agent_id",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.alter_column(
        "agent_api_calls",
        "project_id",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.add_column(
        "agent_api_calls",
        sa.Column("error_class", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_agent_api_calls_error_class",
        "agent_api_calls",
        ["error_class"],
        unique=False,
    )


def downgrade() -> None:
    # Drop in reverse order.  Re-tightening agent_id/project_id back
    # to NOT NULL would only work on databases that have no rows with
    # NULL values; we leave the NULL constraints relaxed on downgrade
    # to avoid bricking the migration on real-world data.  This makes
    # downgrade safe to run repeatedly.
    op.drop_index("ix_agent_api_calls_error_class", table_name="agent_api_calls")
    op.drop_column("agent_api_calls", "error_class")
