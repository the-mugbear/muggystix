"""named endpoints keep their identity: finding_hosts / test_plan_entries
unique per (…, host, name); TESTED observations keyed to their execution result

Revision ID: c3f7a9d2e4b8
Revises: b2e6f8a1c3d7
Create Date: 2026-09-08

Second external review of the named-asset work (e43a9ea):

* ``finding_hosts`` was unique on (finding, host), so promoting the same
  issue on two vhosts of one address kept only the first endpoint.  Now
  unique on (finding, host, name_id) NULLS NOT DISTINCT — unnamed
  (host-level) rows stay one-per-host, named ones one-per-endpoint.
* ``test_plan_entries`` was unique on (plan, host), so a second named target
  on the same host was silently dropped.  Now unique on (plan, host, name_id)
  NULLS NOT DISTINCT.
* ``dns_records.exec_result_id`` (FK, CASCADE) ties a TESTED observation to
  the execution result that is its evidence, with a partial unique index so a
  correction replaces instead of piling up.  Recorded ONLY for an executed
  result that reported the address it reached — never inferred.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "c3f7a9d2e4b8"
down_revision = "b2e6f8a1c3d7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    op.add_column(
        "dns_records",
        sa.Column(
            "exec_result_id", sa.Integer(),
            sa.ForeignKey("test_execution_results.id", ondelete="CASCADE"), nullable=True,
        ),
    )
    op.create_index("ix_dns_records_exec_result_id", "dns_records", ["exec_result_id"])
    bind.execute(sa.text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_dns_record_result_observation ON dns_records "
        "(name_id, record_type, value, exec_result_id) WHERE exec_result_id IS NOT NULL"
    ))

    nnd = " NULLS NOT DISTINCT" if is_pg else ""
    op.drop_constraint("uq_finding_host", "finding_hosts", type_="unique")
    bind.execute(sa.text(
        f"ALTER TABLE finding_hosts ADD CONSTRAINT uq_finding_host_name "
        f"UNIQUE{nnd} (finding_id, host_id, name_id)"
    ))
    op.drop_constraint("uq_plan_host", "test_plan_entries", type_="unique")
    bind.execute(sa.text(
        f"ALTER TABLE test_plan_entries ADD CONSTRAINT uq_plan_host_name "
        f"UNIQUE{nnd} (test_plan_id, host_id, name_id)"
    ))


def downgrade() -> None:
    # Collapse to one row per host before restoring the narrower keys.
    for table, key in (("test_plan_entries", "test_plan_id"), ("finding_hosts", "finding_id")):
        op.execute(
            f"DELETE FROM {table} d USING {table} k "
            f"WHERE d.id > k.id AND d.{key} = k.{key} AND d.host_id = k.host_id"
        )
    op.drop_constraint("uq_plan_host_name", "test_plan_entries", type_="unique")
    op.create_unique_constraint("uq_plan_host", "test_plan_entries", ["test_plan_id", "host_id"])
    op.drop_constraint("uq_finding_host_name", "finding_hosts", type_="unique")
    op.create_unique_constraint("uq_finding_host", "finding_hosts", ["finding_id", "host_id"])
    op.execute("DROP INDEX IF EXISTS uq_dns_record_result_observation")
    op.drop_index("ix_dns_records_exec_result_id", table_name="dns_records")
    op.drop_column("dns_records", "exec_result_id")
