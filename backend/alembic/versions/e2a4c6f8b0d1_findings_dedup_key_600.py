"""findings.dedup_key: 255 → 600, the width of vulnerabilities.issue_key (v2.390.2)

A finding's ``dedup_key`` IS the issue key of the observations it covers
(``observation_judged_on_host`` joins ``Finding.dedup_key ==
Vulnerability.issue_key``), but the column was 255 wide while ``issue_key``
is 600.  A title-keyed issue (``"title:" + normalised title``, titles up to
500) longer than 255 could not be promoted or dismissed at all — Postgres
raised StringDataRightTruncation (review 2026-09-23 C5).  Widening, not
truncating: a clipped key would stop matching its observations.

Downgrade clips to 255 (lossy for keys that needed the width).

Revision ID: e2a4c6f8b0d1
Revises: c6e8a0b2d4f5
Create Date: 2026-09-23
"""
import sqlalchemy as sa
from alembic import op


revision = "e2a4c6f8b0d1"
down_revision = "c6e8a0b2d4f5"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        "findings", "dedup_key",
        existing_type=sa.String(255), type_=sa.String(600), existing_nullable=True,
    )


def downgrade():
    op.alter_column(
        "findings", "dedup_key",
        existing_type=sa.String(600), type_=sa.String(255), existing_nullable=True,
        postgresql_using="left(dedup_key, 255)",
    )
