"""projects.skip_informational_findings — per-project Nessus ingest preference

v2.341.0 — a Nessus batch for a large estate is mostly severity-0
(informational) report items: service detection, cipher lists, traceroute,
CPE.  Each became a ``vulnerabilities`` row carrying its own copy of the
plugin description, solution and output, while posture, insights and risk
scoring already ignore the INFO severity.  The rows cost ingest time,
table size and host-detail payload and buy nothing for analytics.

The project can now say "skip them".  Nullable on purpose: NULL means the
project never chose and the deployment default
(``NESSUS_SKIP_INFORMATIONAL_DEFAULT``) applies — so an operator who sets
the default once covers every project, including the one created on first
boot.  A per-upload form field overrides either.  Ports are still derived
from informational items; only the vulnerability row is skipped.

Revision ID: b3c8e1f47a92
Revises: a8d4f27c1e63
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b3c8e1f47a92"
down_revision: Union[str, None] = "a8d4f27c1e63"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("skip_informational_findings", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("projects", "skip_informational_findings")
