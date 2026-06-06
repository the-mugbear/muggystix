"""agent rate_limit_rpm default 60 -> 240 (v2.84.0)

Pre-v2.84.0, Agent.rate_limit_rpm defaulted to 60 req/min, which is too
tight for the test-execution workflow (load context, list entries, mark
in_progress, post result, sanity check ~ 5-8 calls per test plus host /
service lookups during burst windows).  Bumps:

- column server_default 60 -> 240
- existing rows still at the old default (60) -> 240

Rows that an admin has tuned away from 60 are left alone — that's a
deliberate per-key override and we don't want to clobber it.

The pydantic cap also moves from 600 to 1200 in agents.py, so admins
can dial individual keys higher than the new default when needed.

Revision ID: c8a5e2f91b47
Revises: b6e1f0a3d8c5
Create Date: 2026-05-29
"""
from alembic import op
import sqlalchemy as sa


revision = "c8a5e2f91b47"
down_revision = "b6e1f0a3d8c5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Bump the column default so future inserts pick up 240.
    op.alter_column(
        "agents",
        "rate_limit_rpm",
        server_default="240",
        existing_type=sa.Integer(),
        existing_nullable=False,
    )
    # Lift existing rows that are still on the legacy default.
    op.execute(
        "UPDATE agents SET rate_limit_rpm = 240 WHERE rate_limit_rpm = 60"
    )


def downgrade() -> None:
    op.alter_column(
        "agents",
        "rate_limit_rpm",
        server_default="60",
        existing_type=sa.Integer(),
        existing_nullable=False,
    )
    # Best-effort: roll back rows we previously lifted.  Rows tuned to
    # 240 by an admin will be incorrectly demoted, but downgrade is
    # already a rare/dev path.
    op.execute(
        "UPDATE agents SET rate_limit_rpm = 60 WHERE rate_limit_rpm = 240"
    )
