"""One query history for the Hosts page and the Query page (v2.512.0)

The owner, 2026-10-17: the Query page keeps a history on the server, per
person per project — agent queries included and labelled, refused queries
kept and labelled, the newest 200 kept (FIFO).  The Hosts page already kept
one (`host_query_history`, its newest 50); both are now ONE table and one
service (`query_history_service`), told apart by ``kind``.

**Upgrade**: RENAMES ``host_query_history`` to ``query_history`` (its
indexes, sequence and constraints with it); ADDS ``kind`` ('hosts' for every
existing row), ``error``, ``error_position``, ``by_agent`` (false),
``run_count`` (1) and ``last_run_at`` (from ``created_at``); MERGES a
person's repeated Hosts query into its newest row (``run_count`` = how many
rows it had, ``created_at`` the earliest, ``last_run_at`` the latest) — the
Hosts page collapsed only CONSECUTIVE repeats; then one entry per (person,
project, kind, by_agent, query) is a unique index.

**Downgrade**: DELETES every Query-page and agent row (the old table has no
place for them), drops the new columns and index, renames back.  A merged
Hosts entry stays one row.

Revision ID: a7d3c9e1f5b2
Revises: f6a2c8e4b0d3
"""
import sqlalchemy as sa
from alembic import op

revision = "a7d3c9e1f5b2"
down_revision = "f6a2c8e4b0d3"
branch_labels = None
depends_on = None

_RENAMES = [
    ("ix_host_query_history_id", "ix_query_history_id"),
    ("ix_host_query_history_user_id", "ix_query_history_user_id"),
    ("ix_host_query_history_project_id", "ix_query_history_project_id"),
]
_CONSTRAINTS = [
    ("host_query_history_pkey", "query_history_pkey"),
    ("host_query_history_user_id_fkey", "query_history_user_id_fkey"),
    ("host_query_history_project_id_fkey", "query_history_project_id_fkey"),
]


def upgrade() -> None:
    op.rename_table("host_query_history", "query_history")
    for old, new in _RENAMES:
        op.execute(f"ALTER INDEX {old} RENAME TO {new}")
    for old, new in _CONSTRAINTS:
        op.execute(f"ALTER TABLE query_history RENAME CONSTRAINT {old} TO {new}")
    op.execute("ALTER SEQUENCE host_query_history_id_seq RENAME TO query_history_id_seq")
    op.drop_index("ix_host_query_history_user_project_created", table_name="query_history")

    op.add_column("query_history", sa.Column("kind", sa.String(length=16), server_default="hosts", nullable=False))
    op.add_column("query_history", sa.Column("error", sa.Text(), nullable=True))
    op.add_column("query_history", sa.Column("error_position", sa.Integer(), nullable=True))
    op.add_column("query_history", sa.Column("by_agent", sa.Boolean(), server_default="false", nullable=False))
    op.add_column("query_history", sa.Column("run_count", sa.Integer(), server_default="1", nullable=False))
    op.add_column(
        "query_history",
        sa.Column("last_run_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.execute("UPDATE query_history SET last_run_at = created_at WHERE created_at IS NOT NULL")

    # A repeated query becomes one entry: its newest row carries the count
    # and both ends of its runs; the older rows go.
    op.execute(
        """
        WITH g AS (
            SELECT user_id, project_id, md5(q) AS h, max(id) AS keep, count(*) AS n,
                   min(created_at) AS first_run, max(last_run_at) AS last_run
            FROM query_history GROUP BY user_id, project_id, md5(q) HAVING count(*) > 1
        )
        UPDATE query_history t
        SET run_count = g.n, created_at = g.first_run, last_run_at = g.last_run
        FROM g WHERE t.id = g.keep
        """
    )
    op.execute(
        """
        DELETE FROM query_history a USING query_history b
        WHERE a.user_id = b.user_id AND a.project_id = b.project_id
          AND md5(a.q) = md5(b.q) AND a.id < b.id
        """
    )

    op.create_index(
        "ix_query_history_user_project_kind_last", "query_history",
        ["user_id", "project_id", "kind", "last_run_at"],
    )
    op.create_index(
        "uq_query_history_entry", "query_history",
        ["user_id", "project_id", "kind", "by_agent", sa.text("md5(q)")], unique=True,
    )


def downgrade() -> None:
    op.execute("DELETE FROM query_history WHERE kind <> 'hosts' OR by_agent")
    op.drop_index("uq_query_history_entry", table_name="query_history")
    op.drop_index("ix_query_history_user_project_kind_last", table_name="query_history")
    op.execute("UPDATE query_history SET created_at = last_run_at")
    for column in ("last_run_at", "run_count", "by_agent", "error_position", "error", "kind"):
        op.drop_column("query_history", column)

    op.execute("ALTER SEQUENCE query_history_id_seq RENAME TO host_query_history_id_seq")
    for old, new in _CONSTRAINTS:
        op.execute(f"ALTER TABLE query_history RENAME CONSTRAINT {new} TO {old}")
    for old, new in _RENAMES:
        op.execute(f"ALTER INDEX {new} RENAME TO {old}")
    op.rename_table("query_history", "host_query_history")
    op.create_index(
        "ix_host_query_history_user_project_created", "host_query_history",
        ["user_id", "project_id", "created_at"],
    )
