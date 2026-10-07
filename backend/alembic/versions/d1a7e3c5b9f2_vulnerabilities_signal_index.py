"""partial index over the scanner rows that make a host a signal

The Operations "Untouched, with a reason" queue reads ``vulnerabilities``
twice per request with the filter ``severity = CRITICAL OR severity = HIGH OR
exploitable`` (the per-host counts) and ``severity = CRITICAL OR exploitable``
(the change detector's exclusion).  Neither could use an index, so each was a
scan of the whole table: at 524k rows that was about 280 ms of the statement's
640 ms on a warm cache, and the prod instance (977 MB of scanner rows behind
256 MB of shared buffers) logged the request as SLOW 24 times out of 32.

``ix_vulnerabilities_signal`` holds only the matching rows (about a fifth) and
the three columns the two reads need, so both become index-only scans.

Built inside the upgrade transaction, like every index in this chain (see
CLAUDE.md, "Boot migrations"): well under a second at 524k rows.

Revision ID: d1a7e3c5b9f2
Revises: c9f3b6d8e2a4
Create Date: 2026-10-07
"""
import sqlalchemy as sa
from alembic import op

revision = "d1a7e3c5b9f2"
down_revision = "c9f3b6d8e2a4"
branch_labels = None
depends_on = None

_WHERE = "severity = 'CRITICAL' OR severity = 'HIGH' OR exploitable IS TRUE"


def upgrade():
    op.create_index(
        "ix_vulnerabilities_signal",
        "vulnerabilities",
        ["host_id", "severity", "exploitable"],
        postgresql_where=sa.text(_WHERE),
    )


def downgrade():
    op.drop_index("ix_vulnerabilities_signal", table_name="vulnerabilities")
