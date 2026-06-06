"""collapse duplicate vulnerability rows (v2.72.0)

Overlapping scans (notably manual .nessus uploads) produced duplicate
``vulnerabilities`` rows: the application-level dedup in
``VulnerabilityService._create_vulnerability_from_nessus`` queried for an
existing row but never flushed its own insert, so under the session's
``autoflush=False`` a repeated ``(plugin_id, port)`` within a single scan
was written twice.  The code fix (flush-after-insert) stops new
duplicates; this migration cleans up the ones already on disk.

A "duplicate" here is two+ rows identical on
``(host_id, source, plugin_id, port_id, title)`` — exactly what the
dedup paths treat as the same finding.  We keep the lowest id in each
group (re-stamping it with the group's freshest ``last_seen`` / latest
``scan_id`` so "last seen" stays accurate) and delete the rest.  Nothing
foreign-keys ``vulnerabilities.id``, so the deletes orphan nothing.

Idempotent: a second run finds no groups with COUNT(*) > 1 and no-ops.

Revision ID: e3d9b1c7a4f2
Revises: f1a9c7d2e4b8
Create Date: 2026-05-28
"""
from alembic import op


revision = "e3d9b1c7a4f2"
down_revision = "f1a9c7d2e4b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) Re-stamp the survivor (lowest id per group) with the freshest
    #    observation in the group, so collapsing doesn't lose "last seen".
    op.execute(
        """
        UPDATE vulnerabilities v
        SET last_seen = grp.max_seen,
            scan_id   = grp.latest_scan
        FROM (
            SELECT
                MIN(id) AS keep_id,
                MAX(last_seen) AS max_seen,
                (array_agg(scan_id ORDER BY last_seen DESC NULLS LAST, id DESC))[1] AS latest_scan
            FROM vulnerabilities
            GROUP BY host_id, source, COALESCE(plugin_id, ''), COALESCE(port_id, -1), title
            HAVING COUNT(*) > 1
        ) grp
        WHERE v.id = grp.keep_id
        """
    )

    # 2) Delete every non-survivor (rn > 1 within each group).
    op.execute(
        """
        DELETE FROM vulnerabilities v
        USING (
            SELECT id, ROW_NUMBER() OVER (
                PARTITION BY host_id, source, COALESCE(plugin_id, ''), COALESCE(port_id, -1), title
                ORDER BY id
            ) AS rn
            FROM vulnerabilities
        ) d
        WHERE v.id = d.id AND d.rn > 1
        """
    )


def downgrade() -> None:
    # Irreversible — deleted duplicate rows cannot be reconstructed.
    pass
