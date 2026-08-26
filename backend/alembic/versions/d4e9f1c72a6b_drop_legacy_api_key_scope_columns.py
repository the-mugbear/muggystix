"""contract phase: drop the four legacy api_keys scope FK columns

Every agent key now carries ``agent_session_id`` (expand phase c2d4e6f8a0b1 +
backfill b8e1f37a92c4), and both deps.get_current_agent and every mint path
resolve a key's workflow/scope from it, so the four per-workflow FK columns on
``api_keys`` are dead weight:

    test_plan_id, scope_id, recon_session_id, assist_session_id

This drops them, and rebases the "one active key per plan" partial-unique
backstop (``uq_api_key_plan_active``, keyed on the now-gone ``test_plan_id``)
onto ``agent_session_id`` as ``uq_api_key_agent_session_active``. The per-plan
invariant itself is enforced app-side by revoke-then-mint (`_plan_api_keys`);
this index is the DB backstop against two active keys pointing at one session.

Guarded: aborts if any ACTIVE agent key lacks ``agent_session_id`` (it would be
locked out by get_current_agent once the columns are gone), and deduplicates any
accidental multiple-active-keys-per-agent_session before the unique index.

Revision ID: d4e9f1c72a6b
Revises: 7ce9026992ec
Create Date: 2026-08-26
"""
from alembic import op
import sqlalchemy as sa


revision = "d4e9f1c72a6b"
down_revision = "7ce9026992ec"
branch_labels = None
depends_on = None

# (column, referenced table) — order is irrelevant for the drop.
_LEGACY_COLS = [
    ("test_plan_id", "test_plans"),
    ("scope_id", "scopes"),
    ("recon_session_id", "recon_sessions"),
    ("assist_session_id", "assist_sessions"),
]


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Guard: no ACTIVE agent key may lack the unified binding — post-contract
    #    get_current_agent refuses such a key, so dropping the legacy columns
    #    would lock it out silently. Fail loud instead.
    orphaned = bind.execute(sa.text(
        "SELECT count(*) FROM api_keys "
        "WHERE is_active AND agent_id IS NOT NULL AND agent_session_id IS NULL"
    )).scalar()
    if orphaned:
        raise RuntimeError(
            f"{orphaned} active agent API key(s) have no agent_session_id — refusing "
            "to drop the legacy scope columns and lock them out. End the owning "
            "workflow session(s) to revoke them, then re-run."
        )

    # 2. Rebase the one-active-key-per-session backstop onto agent_session_id.
    #    Deactivate all-but-newest active key per agent_session first so the
    #    CREATE UNIQUE can't trip on a pre-existing dup (revoke-then-mint should
    #    already guarantee none; this is defensive and idempotent).
    bind.execute(sa.text(
        "UPDATE api_keys SET is_active = false WHERE id IN ("
        "  SELECT id FROM ("
        "    SELECT id, row_number() OVER ("
        "      PARTITION BY agent_session_id ORDER BY id DESC"
        "    ) AS rn FROM api_keys"
        "    WHERE is_active AND agent_session_id IS NOT NULL"
        "  ) d WHERE d.rn > 1"
        ")"
    ))
    op.drop_index("uq_api_key_plan_active", table_name="api_keys")
    op.execute(
        "CREATE UNIQUE INDEX uq_api_key_agent_session_active "
        "ON api_keys (agent_session_id) "
        "WHERE is_active AND agent_session_id IS NOT NULL"
    )

    # 3. Drop the four legacy scope FK columns. Their FK constraint and
    #    single-column index drop with the column.
    for col, _ in _LEGACY_COLS:
        op.drop_column("api_keys", col)


def downgrade() -> None:
    # Schema-only reversal: the binding VALUES lived only in these columns and
    # are not recoverable, but the structure is (nullable, so no backfill).
    for col, target in _LEGACY_COLS:
        op.add_column("api_keys", sa.Column(col, sa.Integer(), nullable=True))
        op.create_foreign_key(
            f"api_keys_{col}_fkey", "api_keys", target, [col], ["id"], ondelete="CASCADE",
        )
        op.create_index(f"ix_api_keys_{col}", "api_keys", [col])
    op.drop_index("uq_api_key_agent_session_active", table_name="api_keys")
    op.execute(
        "CREATE UNIQUE INDEX uq_api_key_plan_active "
        "ON api_keys (test_plan_id) "
        "WHERE is_active = true AND test_plan_id IS NOT NULL"
    )
