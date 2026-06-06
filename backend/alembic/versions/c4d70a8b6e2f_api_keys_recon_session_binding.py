"""api_keys.recon_session_id — bind recon keys to their session, not just the scope

Revision ID: c4d70a8b6e2f
Revises: f9e2d471a8c6
Create Date: 2026-05-18 23:10:00.000000

Concurrent-recon collision bug: pre-fix, two simultaneous recon
sessions on the same scope caused ``_load_recon_session`` (used by
every recon endpoint that doesn't take a session_id in the URL —
/recon/context, /recon/upload, /recon/summary, /recon/complete) to
silently route both agents' calls to the most-recently-started
active session.  Result: Agent A's scanner uploads landed on Agent
B's session, audit attribution swapped halfway through a run.

Root cause: API keys minted for recon bound only to ``scope_id``.
The plan-scoped equivalent (``test_plan_id``) has been correct
since v2.11.0; recon never got the same treatment.

This migration adds ``api_keys.recon_session_id`` (FK, nullable).
Backfill is best-effort: for each existing recon-scoped key,
populate from the most-recently-started ACTIVE session on the
key's scope_id.  Keys whose sessions are already terminal aren't
backfilled — they stay NULL and the loader falls back to the
legacy heuristic for those (acceptable since their sessions are
closed and the heuristic produces a 404 anyway).

For all NEW keys minted post-migration, the /scopes/{id}/recon/start
endpoint MUST populate this column.  Enforced via the model + service
layer; the column itself stays nullable to avoid bricking legacy keys.
"""
from alembic import op
import sqlalchemy as sa


revision = "c4d70a8b6e2f"
down_revision = "f9e2d471a8c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "api_keys",
        sa.Column("recon_session_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_api_keys_recon_session_id",
        "api_keys",
        "recon_sessions",
        ["recon_session_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_api_keys_recon_session_id",
        "api_keys",
        ["recon_session_id"],
        unique=False,
    )

    # Best-effort backfill: pin each existing scope-bound key to the
    # most-recent ACTIVE session on its scope.  Done in SQL so it
    # works without loading the full ORM stack inside the migration.
    op.execute(
        """
        UPDATE api_keys AS k
        SET recon_session_id = sub.session_id
        FROM (
            SELECT DISTINCT ON (scope_id) scope_id, id AS session_id
            FROM recon_sessions
            WHERE status = 'active'
            ORDER BY scope_id, started_at DESC
        ) AS sub
        WHERE k.scope_id IS NOT NULL
          AND k.recon_session_id IS NULL
          AND k.scope_id = sub.scope_id
        """
    )


def downgrade() -> None:
    op.drop_index("ix_api_keys_recon_session_id", table_name="api_keys")
    op.drop_constraint(
        "fk_api_keys_recon_session_id", "api_keys", type_="foreignkey"
    )
    op.drop_column("api_keys", "recon_session_id")
