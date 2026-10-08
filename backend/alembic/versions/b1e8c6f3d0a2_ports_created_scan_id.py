"""ports_v2.created_scan_id replaces ingestion_jobs.in_progress_created_port_ids

A failed import removes the ports only it created.  An import that writes no
port history (Nessus) had no record of which ones those were, so it kept a
JSON list of their ids on its job row, rewritten with every batch.  The record
now sits on the port itself:

* ``ports_v2.created_scan_id`` — the scan whose import inserted the row
  (``ON DELETE SET NULL``, indexed; the index is built inside the upgrade
  transaction).  Existing rows stay NULL, which the cleanup reads as "not this
  attempt's": nothing that exists today can be deleted through the new column.
* The one exception is carried over, so an attempt that was interrupted before
  the upgrade is still cleaned up after it: a port named in a job's list is
  stamped with that job's in-progress scan.
* ``ingestion_jobs.in_progress_created_port_ids`` is dropped.  It was read
  only by the cleanup.

The downgrade re-creates the list column and fills it from the stamps of the
scans that are still in progress, then drops the stamp.

Revision ID: b1e8c6f3d0a2
Revises: a0d7b5e2c9f1
Create Date: 2026-10-08
"""
import sqlalchemy as sa
from alembic import op

revision = "b1e8c6f3d0a2"
down_revision = "a0d7b5e2c9f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ports_v2",
        sa.Column(
            "created_scan_id", sa.Integer(),
            sa.ForeignKey("scans.id", ondelete="SET NULL"), nullable=True,
        ),
    )
    op.execute(
        """
        UPDATE ports_v2 AS p
           SET created_scan_id = j.in_progress_scan_id
          FROM ingestion_jobs AS j
         CROSS JOIN LATERAL json_array_elements_text(
                   CASE WHEN json_typeof(j.in_progress_created_port_ids) = 'array'
                        THEN j.in_progress_created_port_ids ELSE '[]'::json END
               ) AS named(port_id)
         WHERE j.in_progress_scan_id IS NOT NULL
           AND named.port_id ~ '^[0-9]{1,9}$'
           AND p.id = named.port_id::integer
        """
    )
    op.create_index("ix_ports_v2_created_scan_id", "ports_v2", ["created_scan_id"], unique=False)
    op.drop_column("ingestion_jobs", "in_progress_created_port_ids")


def downgrade() -> None:
    op.add_column(
        "ingestion_jobs",
        sa.Column("in_progress_created_port_ids", sa.JSON(), nullable=True),
    )
    op.execute(
        """
        UPDATE ingestion_jobs AS j
           SET in_progress_created_port_ids = created.ids
          FROM (
                SELECT p.created_scan_id AS scan_id, json_agg(p.id ORDER BY p.id) AS ids
                  FROM ports_v2 AS p
                 WHERE p.created_scan_id IN (
                           SELECT in_progress_scan_id FROM ingestion_jobs
                            WHERE in_progress_scan_id IS NOT NULL
                       )
                 GROUP BY p.created_scan_id
               ) AS created
         WHERE j.in_progress_scan_id = created.scan_id
        """
    )
    op.drop_index("ix_ports_v2_created_scan_id", table_name="ports_v2")
    # Dropping the column drops its foreign key with it.
    op.drop_column("ports_v2", "created_scan_id")
