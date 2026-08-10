"""webhook delivery claim token

Revision ID: c9a41e7b2d68
Revises: b8d3e05f7a21
Create Date: 2026-08-10

Review A6 — the outbox could POST the same event twice.

``_claim_for_send`` re-read ``status == 'pending'`` before sending, which is
check-then-act, not a claim. The sweeper leaves a row ``pending`` while its
own POST is in flight, so a fast-path task that had been sitting in the
bounded queue past its 60s lease could read ``pending`` and send the same
delivery the sweeper was already sending. Payloads carry no idempotency key,
so receivers could not dedupe it either.

This adds the ownership token. Claiming becomes one atomic UPDATE that flips
the row to the new ``sending`` state and stamps a token; only the token holder
may record the outcome. ``next_attempt_at`` doubles as the lease expiry while
``sending``, so a sender that dies mid-POST is reclaimed on lease expiry
instead of wedging the row.

``sending`` needs no DDL — ``status`` is already a free-form String(20).
"""
from alembic import op
import sqlalchemy as sa

revision = "c9a41e7b2d68"
down_revision = "b8d3e05f7a21"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "webhook_deliveries",
        sa.Column("claim_token", sa.String(length=36), nullable=True),
    )


def downgrade() -> None:
    # Any row mid-flight when this is rolled back would be left in a state the
    # old code doesn't understand ('sending' is invisible to its pending-only
    # sweeper query, so the row would never be retried). Hand them back as due
    # pending rows before dropping the column.
    op.execute(
        "UPDATE webhook_deliveries "
        "SET status = 'pending', next_attempt_at = NOW() "
        "WHERE status = 'sending'"
    )
    op.drop_column("webhook_deliveries", "claim_token")
