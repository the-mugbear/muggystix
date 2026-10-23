"""A removed endpoint's Undo record is typed columns, not a JSON blob

Review 2026-10-23b, TRI-5 (the column-vs-blob invariant).  Since 2.526.0 an
endpoint removal's history line kept ``{host_id, name_id,
retest_requested_at}`` as JSON in ``before_value`` (field-less), and every
restore read and parsed all of the finding's removal lines in Python to find
the latest one of its endpoint.  ``before_value`` also meant two things: an
edit's shown value and that hidden record.

**Upgrade**: ADDS ``removed_host_id`` (FK ``hosts_v2``, SET NULL),
``removed_name_id`` (FK ``dns_names``, SET NULL) and
``removed_retest_requested_at`` to ``finding_status_history``, with an index
on ``(removed_host_id, finding_id)`` (the Undo's lookup, and the FK index)
and one on ``removed_name_id``.  Each field-less line whose ``before_value``
is such a record is moved into them (a host or name deleted since is left
NULL — nothing to restore) and its ``before_value`` cleared.  No other row
changes.

**Downgrade**: writes each removal line's record back into ``before_value``
as the JSON 2.526.0 reads, then drops the indexes and the three columns.

Revision ID: d4a8c2e6f1b3
Revises: 7b83a791a783
"""
import sqlalchemy as sa
from alembic import op

revision = "d4a8c2e6f1b3"
down_revision = "7b83a791a783"
branch_labels = None
depends_on = None

# The only writer of a field-less ``before_value`` wrote this JSON object.
_RECORD = "field IS NULL AND before_value IS NOT NULL AND left(before_value, 1) = '{'"


def upgrade() -> None:
    op.add_column("finding_status_history", sa.Column(
        "removed_host_id", sa.Integer(), sa.ForeignKey("hosts_v2.id", ondelete="SET NULL"), nullable=True))
    op.add_column("finding_status_history", sa.Column(
        "removed_name_id", sa.Integer(), sa.ForeignKey("dns_names.id", ondelete="SET NULL"), nullable=True))
    op.add_column("finding_status_history", sa.Column(
        "removed_retest_requested_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_finding_status_history_removed_host", "finding_status_history",
                    ["removed_host_id", "finding_id"])
    op.create_index("ix_finding_status_history_removed_name_id", "finding_status_history", ["removed_name_id"])
    op.execute(f"""
        UPDATE finding_status_history AS h SET
            removed_host_id = (SELECT id FROM hosts_v2 WHERE id = (h.before_value::jsonb ->> 'host_id')::int),
            removed_name_id = (SELECT id FROM dns_names WHERE id = (h.before_value::jsonb ->> 'name_id')::int),
            removed_retest_requested_at = (h.before_value::jsonb ->> 'retest_requested_at')::timestamptz,
            before_value = NULL
        WHERE {_RECORD}
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE finding_status_history SET before_value = json_build_object(
            'host_id', removed_host_id, 'name_id', removed_name_id,
            'retest_requested_at', to_char(removed_retest_requested_at AT TIME ZONE 'UTC',
                                           'YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"')
        )::text
        WHERE field IS NULL AND removed_host_id IS NOT NULL
    """)
    op.drop_index("ix_finding_status_history_removed_name_id", table_name="finding_status_history")
    op.drop_index("ix_finding_status_history_removed_host", table_name="finding_status_history")
    op.drop_column("finding_status_history", "removed_retest_requested_at")
    op.drop_column("finding_status_history", "removed_name_id")
    op.drop_column("finding_status_history", "removed_host_id")
