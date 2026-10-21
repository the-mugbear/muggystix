"""Agent feedback gaps: a revised proposal, evidence with no host, the raw output's hash (v2.519.0)

Prod agent feedback, 2026-10-20 (owner: "address the gaps you identified as
real gaps"):

* An agent could not revise its own pending proposal — a corrected draft sat
  beside the wrong one for a person to sort out.  A proposal now names the one
  that REPLACED it (``agent_proposals.replaced_by_id``).
* Evidence needed a host, so context about no single host (a domain-wide
  lookup, a scope note) could not be recorded.  ``evidence_records.host_id``
  may be empty for an ``info`` record that answers no test and names no
  endpoint (``ck_evidence_host_or_info``).
* An agent could not check that it read a raw output whole.  Each record keeps
  the SHA-256 of its raw output (``raw_output_sha256``), computed here for the
  records already stored.

**Upgrade**: ADDS ``agent_proposals.replaced_by_id`` (FK to itself, SET NULL,
indexed); makes ``evidence_records.host_id`` NULLABLE with the check; ADDS
``evidence_records.raw_output_sha256`` and fills it for every record with raw
output (one UPDATE; the hash of the stored text's UTF-8 bytes).

**Downgrade**: DELETES the evidence records with no host (the old column
cannot hold them), then restores NOT NULL; drops the hash and the link.

Revision ID: c4f9e2a7b1d5
Revises: b8e4d2f6a0c3
"""
import sqlalchemy as sa
from alembic import op

revision = "c4f9e2a7b1d5"
down_revision = "b8e4d2f6a0c3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_proposals", sa.Column("replaced_by_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "agent_proposals_replaced_by_id_fkey", "agent_proposals", "agent_proposals",
        ["replaced_by_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_agent_proposals_replaced_by_id", "agent_proposals", ["replaced_by_id"])

    op.alter_column("evidence_records", "host_id", existing_type=sa.Integer(), nullable=True)
    op.create_check_constraint(
        "ck_evidence_host_or_info", "evidence_records",
        "host_id IS NOT NULL OR (outcome = 'info' AND host_test_id IS NULL AND finding_host_id IS NULL)",
    )

    op.add_column("evidence_records", sa.Column("raw_output_sha256", sa.String(length=64), nullable=True))
    op.execute(
        "UPDATE evidence_records SET raw_output_sha256 = encode(sha256(convert_to(raw_output, 'UTF8')), 'hex') "
        "WHERE raw_output IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_column("evidence_records", "raw_output_sha256")
    op.drop_constraint("ck_evidence_host_or_info", "evidence_records", type_="check")
    op.execute("DELETE FROM evidence_records WHERE host_id IS NULL")
    op.alter_column("evidence_records", "host_id", existing_type=sa.Integer(), nullable=False)
    op.drop_index("ix_agent_proposals_replaced_by_id", table_name="agent_proposals")
    op.drop_constraint("agent_proposals_replaced_by_id_fkey", "agent_proposals", type_="foreignkey")
    op.drop_column("agent_proposals", "replaced_by_id")
