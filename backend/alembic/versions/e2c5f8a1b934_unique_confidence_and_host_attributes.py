"""dedupe + UNIQUE on host/port confidence and host_attributes

Revision ID: e2c5f8a1b934
Revises: d1b7e4a9c602
Create Date: 2026-06-10 22:30:00.000000

The 2026-06-10 schema review flagged that host_confidence, port_confidence
and host_attributes carried NO unique constraint, so re-scans could silently
accrete duplicate rows even though every write path already check-then-upserts
on a fixed key:

  * host_confidence / port_confidence — one winning row per (subject, field);
    _track_field_confidence updates in place.
  * host_attributes — one row per (host_id, attribute_type, value, source);
    _create_host_attribute upserts on that key (a distinct value from the same
    source is a genuinely different attribute, so value is part of the key).

This migration dedupes any existing rows (keeping, for the confidence tables,
the highest-confidence row per group; for host_attributes, the newest by id),
then adds the matching UNIQUE constraints.  The non-unique
(subject, field) indexes are dropped — the unique constraints' indexes serve
the same lookups.

Postgres-only DELETE ... USING; this project only ever migrates Postgres (the
SQLite test path builds the schema from the models via create_all, which now
declare these constraints directly).

Phase 1.3 of the schema-review remediation.
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'e2c5f8a1b934'
down_revision: Union[str, None] = 'd1b7e4a9c602'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- host_confidence: keep the highest-confidence row per (host, field) ---
    op.execute(
        """
        DELETE FROM host_confidence a USING host_confidence b
        WHERE a.host_id = b.host_id AND a.field_name = b.field_name
          AND (a.confidence_score < b.confidence_score
               OR (a.confidence_score = b.confidence_score AND a.id < b.id))
        """
    )
    op.execute("DROP INDEX IF EXISTS idx_host_confidence_host_field")
    op.create_unique_constraint(
        "uq_host_confidence_host_field", "host_confidence", ["host_id", "field_name"]
    )

    # --- port_confidence: same, per (port, field) ---
    op.execute(
        """
        DELETE FROM port_confidence a USING port_confidence b
        WHERE a.port_id = b.port_id AND a.field_name = b.field_name
          AND (a.confidence_score < b.confidence_score
               OR (a.confidence_score = b.confidence_score AND a.id < b.id))
        """
    )
    op.execute("DROP INDEX IF EXISTS idx_port_confidence_port_field")
    op.create_unique_constraint(
        "uq_port_confidence_port_field", "port_confidence", ["port_id", "field_name"]
    )

    # --- host_attributes: keep newest per (host_id, attribute_type, value, source) ---
    op.execute(
        """
        DELETE FROM host_attributes a USING host_attributes b
        WHERE a.host_id = b.host_id AND a.attribute_type = b.attribute_type
          AND a.value = b.value AND a.source = b.source AND a.id < b.id
        """
    )
    op.create_unique_constraint(
        "uq_host_attribute_key", "host_attributes",
        ["host_id", "attribute_type", "value", "source"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_host_attribute_key", "host_attributes", type_="unique")

    op.drop_constraint("uq_port_confidence_port_field", "port_confidence", type_="unique")
    op.create_index("idx_port_confidence_port_field", "port_confidence", ["port_id", "field_name"])

    op.drop_constraint("uq_host_confidence_host_field", "host_confidence", type_="unique")
    op.create_index("idx_host_confidence_host_field", "host_confidence", ["host_id", "field_name"])
