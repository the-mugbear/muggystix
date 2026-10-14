"""finding_hosts.remediated_at — when an endpoint was concluded fixed

Owner, 2026-10-14: the remediation deadlines page measures the time to
remediate (MTTR) from a finding on a host's assigned date to the day the
assessor marked its endpoint ``remediated``.  Nothing stored that day: the
move is a line of text in the finding's history.  From this revision
``FindingService._move_endpoint`` (the one writer of an endpoint's state)
stamps it, and clears it when the endpoint leaves ``remediated``.

**Upgrade.**  Adds the nullable column, then back-fills each endpoint that is
``remediated`` now from the LATEST history line of its finding that records
that move — ``Endpoint <label>: <old> → remediated`` (optionally followed by
`` — <note>``), where ``<label>`` is the host's address, or ``<name> on
<address>`` for a named endpoint, as ``FindingService._endpoint_label``
writes it.  An endpoint with no such line stays NULL (counted as Remediated,
not measured).  Nothing else is written.

**Downgrade.**  Drops the column: the stamped days are lost.

One transaction; a failure leaves the database at ``f3a9c1e7d5b2``.

Revision ID: b7d2e5f9a1c3
Revises: f3a9c1e7d5b2
"""
import sqlalchemy as sa
from alembic import op

revision = "b7d2e5f9a1c3"
down_revision = "f3a9c1e7d5b2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("finding_hosts", sa.Column("remediated_at", sa.DateTime(timezone=True), nullable=True))
    # The label as `_endpoint_label` writes it; the line's remainder after
    # "Endpoint <label>: " must be "<old state> → remediated", alone or
    # followed by " — <note>".  starts_with / substr, not LIKE: a name may
    # hold "_" or "%".
    op.execute(sa.text("""
        WITH labelled AS (
            SELECT fh.id, fh.finding_id,
                   'Endpoint ' || CASE WHEN dn.id IS NOT NULL THEN dn.fqdn || ' on ' || h.ip_address
                                       ELSE h.ip_address END || ': ' AS prefix
            FROM finding_hosts fh
            JOIN hosts_v2 h ON h.id = fh.host_id
            LEFT JOIN dns_names dn ON dn.id = fh.name_id
            WHERE fh.host_status = 'remediated'
        ), moved AS (
            SELECT l.id, max(fsh.created_at) AS at
            FROM labelled l
            JOIN finding_status_history fsh ON fsh.finding_id = l.finding_id
            WHERE starts_with(fsh.summary, l.prefix)
              AND substr(fsh.summary, length(l.prefix) + 1) ~ '^[a-z_]+ → remediated( — .*)?$'
            GROUP BY l.id
        )
        UPDATE finding_hosts fh SET remediated_at = moved.at
        FROM moved WHERE moved.id = fh.id
    """))


def downgrade():
    op.drop_column("finding_hosts", "remediated_at")
