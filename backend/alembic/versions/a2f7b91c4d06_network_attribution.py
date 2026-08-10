"""network_attributions: registry + hosting provenance per netblock

Answers "is this host really the client's?" — today's out-of-scope check only
validates hosts against CIDRs someone typed into the scope, which is
self-referential. Keyed on the netblock (RDAP answers about a CIDR, not a
host); hosts attach through host_network_attributions via the same IPTrie
correlation pass that maps hosts to scope subnets.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a2f7b91c4d06"
down_revision: Union[str, None] = "f1c8d5a03e79"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "network_attributions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("cidr", sa.String(length=64), nullable=False),
        sa.Column("asn", sa.Integer(), nullable=True),
        sa.Column("as_name", sa.String(length=255), nullable=True),
        sa.Column("org_name", sa.String(length=255), nullable=True),
        sa.Column("country", sa.String(length=8), nullable=True),
        sa.Column("registry", sa.String(length=32), nullable=True),
        sa.Column("handle", sa.String(length=64), nullable=True),
        sa.Column("cloud_provider", sa.String(length=32), nullable=True),
        sa.Column("cloud_region", sa.String(length=64), nullable=True),
        sa.Column("cloud_service", sa.String(length=64), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=False, server_default="rdap"),
        sa.Column("raw", sa.JSON(), nullable=True),
        sa.Column("looked_up_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "cidr", "source", name="uq_attribution_block"),
    )
    op.create_index("ix_network_attributions_id", "network_attributions", ["id"])
    op.create_index("ix_network_attributions_project_id", "network_attributions", ["project_id"])
    op.create_index("ix_network_attributions_cidr", "network_attributions", ["cidr"])
    op.create_index("ix_network_attributions_asn", "network_attributions", ["asn"])
    op.create_index("ix_network_attributions_org_name", "network_attributions", ["org_name"])
    op.create_index("ix_network_attributions_cloud_provider", "network_attributions", ["cloud_provider"])
    op.create_index("idx_attribution_project_asn", "network_attributions", ["project_id", "asn"])

    op.create_table(
        "host_network_attributions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("host_id", sa.Integer(), nullable=False),
        sa.Column("attribution_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["host_id"], ["hosts_v2.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["attribution_id"], ["network_attributions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("host_id", "attribution_id", name="uq_host_attribution"),
    )
    op.create_index("ix_host_network_attributions_id", "host_network_attributions", ["id"])
    op.create_index("ix_host_network_attributions_host_id", "host_network_attributions", ["host_id"])
    op.create_index(
        "ix_host_network_attributions_attribution_id", "host_network_attributions", ["attribution_id"],
    )


def downgrade() -> None:
    op.drop_table("host_network_attributions")
    op.drop_table("network_attributions")
