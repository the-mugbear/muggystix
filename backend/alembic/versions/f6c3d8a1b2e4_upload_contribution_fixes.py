"""upload-contribution fixes: findings keep their first scan; partial imports

Two schema changes from the scans contribution review (2026-09-10):

1. ``vulnerabilities.scan_id`` becomes "first recorded by" and stops moving.
   It used to be reassigned to whichever upload last saw the finding, so the
   per-scan severity rollup on /scans changed retroactively — and with ON
   DELETE CASCADE, deleting the newest scan deleted findings first reported
   by an older one.  Now nullable with ON DELETE SET NULL (a finding outlives
   the scan that introduced it), plus ``last_seen_scan_id`` for the
   re-observation.  No backfill of ``scan_id``: the original first scan was
   overwritten and is unrecoverable; existing rows keep whatever scan last
   touched them.  ``last_seen_scan_id`` is backfilled from that same value,
   which IS what it meant.

2. ``ingestion_jobs.partial`` — the parser stopped early (truncated file).
   Parsers have published this since v2.232.0; nothing persisted it, so a
   truncated nmap file reached the UI as "1 record skipped".

Revision ID: f6c3d8a1b2e4
Revises: e5b7a2c9d4f1
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "f6c3d8a1b2e4"
down_revision = "e5b7a2c9d4f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- vulnerabilities: first-seen scan, SET NULL ---------------------
    op.alter_column(
        "vulnerabilities", "scan_id", existing_type=sa.Integer(), nullable=True
    )
    op.drop_constraint(
        "vulnerabilities_scan_id_fkey", "vulnerabilities", type_="foreignkey"
    )
    op.create_foreign_key(
        "vulnerabilities_scan_id_fkey",
        "vulnerabilities",
        "scans",
        ["scan_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "vulnerabilities",
        sa.Column("last_seen_scan_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "vulnerabilities_last_seen_scan_id_fkey",
        "vulnerabilities",
        "scans",
        ["last_seen_scan_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_vulnerabilities_last_seen_scan_id",
        "vulnerabilities",
        ["last_seen_scan_id"],
    )
    # The old scan_id was "last scan that saw it" — exactly what the new
    # column means, so it is the one honest backfill available.
    op.execute("UPDATE vulnerabilities SET last_seen_scan_id = scan_id")

    # --- ingestion_jobs.partial ----------------------------------------
    op.add_column(
        "ingestion_jobs",
        sa.Column(
            "partial",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("ingestion_jobs", "partial")

    op.drop_index("ix_vulnerabilities_last_seen_scan_id", table_name="vulnerabilities")
    op.drop_constraint(
        "vulnerabilities_last_seen_scan_id_fkey", "vulnerabilities", type_="foreignkey"
    )
    op.drop_column("vulnerabilities", "last_seen_scan_id")
    op.drop_constraint(
        "vulnerabilities_scan_id_fkey", "vulnerabilities", type_="foreignkey"
    )
    # Rows whose first scan was deleted since the upgrade have no scan to
    # return to; drop them rather than fail the NOT NULL restore.
    op.execute("DELETE FROM vulnerabilities WHERE scan_id IS NULL")
    op.create_foreign_key(
        "vulnerabilities_scan_id_fkey",
        "vulnerabilities",
        "scans",
        ["scan_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.alter_column(
        "vulnerabilities", "scan_id", existing_type=sa.Integer(), nullable=False
    )
