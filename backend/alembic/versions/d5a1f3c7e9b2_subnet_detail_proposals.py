"""Agents propose a subnet's description and site (v2.522.0)

Prod agent feedback #76, 2026-10-22: an operator asked their agent to set the
site and description of scope subnets, and the agent surface had no way to.
Owner: "Let an agent set a subnet site and description as a proposal".  A
``subnet_details`` proposal names its subnet (``agent_proposals.subnet_id``);
deleting the subnet deletes its proposals, as deleting an endpoint does.

**Upgrade**: ADDS ``agent_proposals.subnet_id`` (FK to ``subnets``, CASCADE,
indexed).  No data changes.

**Downgrade**: DELETES every ``subnet_details`` proposal (the old code has no
such kind), then drops the column.

Revision ID: d5a1f3c7e9b2
Revises: c4f9e2a7b1d5
"""
import sqlalchemy as sa
from alembic import op

revision = "d5a1f3c7e9b2"
down_revision = "c4f9e2a7b1d5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_proposals", sa.Column("subnet_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "agent_proposals_subnet_id_fkey", "agent_proposals", "subnets",
        ["subnet_id"], ["id"], ondelete="CASCADE",
    )
    op.create_index("ix_agent_proposals_subnet_id", "agent_proposals", ["subnet_id"])


def downgrade() -> None:
    # A proposal superseded by one of these points at it (replaced_by_id,
    # SET NULL), so deleting them needs nothing else first.
    op.execute("DELETE FROM agent_proposals WHERE kind = 'subnet_details'")
    op.drop_index("ix_agent_proposals_subnet_id", table_name="agent_proposals")
    op.drop_constraint("agent_proposals_subnet_id_fkey", "agent_proposals", type_="foreignkey")
    op.drop_column("agent_proposals", "subnet_id")
