"""The retest workflow: one retest state (each host's), the day it was asked
for, and "Reported fixed" always dated

Owner, 2026-10-14 (the retest walkthrough): a retest is done per host, and
the finding-level "Retest" status — a second, unconnected meaning of the
same word — is retired.  A retest is closed with evidence recorded since it
was asked for, so the day it was asked for is stored.

**Upgrade** (one transaction):

1. ADDS ``finding_hosts.retest_requested_at`` (nullable).  An endpoint already
   in ``retest`` keeps NULL: any retest result recorded on it counts.
2. Every finding whose status is ``retest`` becomes ``open`` (still under
   investigation, as ``retest`` was), with a history line saying why; its
   endpoints that were ``open`` ("Still present") become ``retest``, stamped
   now.  Endpoints in any other state are left alone.
3. A remediation record ``closed`` ("Reported fixed") with no ``closed_on`` is
   dated from the latest timeline entry that recorded the move to ``closed``
   (its ``occurred_at`` day, UTC).  One with no such entry stays undated.
4. "Reported fixed" asks for the retest from now on, so a claim made before
   this revision does too: the endpoint of a ``closed`` record that is still
   ``open`` becomes ``retest`` (stamped), with a history line.  (No
   notification: nobody acted; the endpoints are on the retest list.)

**Downgrade.**  Drops the column.  The findings moved off ``retest`` are NOT
put back (which were is only in their history lines), and the back-filled
dates stay.

Revision ID: e5c1a7b3d9f2
Revises: d4b8e2a6c9f3
"""
import sqlalchemy as sa
from alembic import op

revision = "e5c1a7b3d9f2"
down_revision = "d4b8e2a6c9f3"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("finding_hosts", sa.Column("retest_requested_at", sa.DateTime(timezone=True), nullable=True))

    op.execute(sa.text("""
        INSERT INTO finding_status_history (finding_id, from_status, to_status, changed_by_id, summary, created_at)
        SELECT f.id, 'retest', 'open', NULL,
               'The finding-level Retest status is retired: a retest is now per host. '
               || 'This finding is under investigation; its hosts that were still present are Retest pending.',
               now()
        FROM findings f WHERE f.status = 'retest'
    """))
    op.execute(sa.text("""
        UPDATE finding_hosts fh SET host_status = 'retest', retest_requested_at = now()
        FROM findings f
        WHERE f.id = fh.finding_id AND f.status = 'retest' AND fh.host_status = 'open'
    """))
    op.execute(sa.text("UPDATE findings SET status = 'open' WHERE status = 'retest'"))

    op.execute(sa.text("""
        UPDATE finding_host_remediation r SET closed_on = moved.day
        FROM (
            SELECT DISTINCT ON (e.finding_host_id) e.finding_host_id, (e.occurred_at AT TIME ZONE 'UTC')::date AS day
            FROM remediation_events e
            WHERE e.kind = 'change' AND e.field = 'status' AND e.new_value = 'closed'
              AND e.finding_host_id IS NOT NULL
            ORDER BY e.finding_host_id, e.occurred_at DESC
        ) moved
        WHERE r.finding_host_id = moved.finding_host_id AND r.status = 'closed' AND r.closed_on IS NULL
    """))

    op.execute(sa.text("""
        INSERT INTO finding_status_history (finding_id, from_status, to_status, changed_by_id, summary, created_at)
        SELECT f.id, f.status, f.status, NULL,
               'Endpoint ' || CASE WHEN dn.id IS NOT NULL THEN dn.fqdn || ' on ' || h.ip_address
                                   ELSE h.ip_address END
               || ': open → retest — the contact had reported it fixed (retests are asked for since this upgrade)',
               now()
        FROM finding_host_remediation r
        JOIN finding_hosts fh ON fh.id = r.finding_host_id
        JOIN findings f ON f.id = fh.finding_id
        JOIN hosts_v2 h ON h.id = fh.host_id
        LEFT JOIN dns_names dn ON dn.id = fh.name_id
        WHERE r.status = 'closed' AND fh.host_status = 'open'
    """))
    op.execute(sa.text("""
        UPDATE finding_hosts fh SET host_status = 'retest', retest_requested_at = now()
        FROM finding_host_remediation r
        WHERE r.finding_host_id = fh.id AND r.status = 'closed' AND fh.host_status = 'open'
    """))


def downgrade():
    op.drop_column("finding_hosts", "retest_requested_at")
