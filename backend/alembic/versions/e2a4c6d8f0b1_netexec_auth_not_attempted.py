"""netexec_results.auth_success: NULL for rows that are not a login result (v2.388.1)

Every NetExec row defaulted to ``auth_success = false``: the SMB banner line
and a spider_plus listing read "Auth failed" in the inspector, and — the
weak-auth condition keeps the latest row per (host, protocol, port) — such a
row could hide a real guest login.  A failed login is a ``[-]`` line; any
other false row was never an attempt.

Downgrade restores false.

Revision ID: e2a4c6d8f0b1
Revises: d8f1a3c5e7b9
Create Date: 2026-09-23
"""
from alembic import op


revision = "e2a4c6d8f0b1"
down_revision = "d8f1a3c5e7b9"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        UPDATE netexec_results SET auth_success = NULL
        WHERE auth_success IS FALSE AND coalesce(raw_output, '') NOT LIKE '%[-]%'
        """
    )


def downgrade():
    op.execute("UPDATE netexec_results SET auth_success = false WHERE auth_success IS NULL")
