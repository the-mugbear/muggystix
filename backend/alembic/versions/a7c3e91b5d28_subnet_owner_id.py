"""subnet owner_id — accountable IT contact per network range

Revision ID: a7c3e91b5d28
Revises: b2e6d9f04a17
Create Date: 2026-06-10 00:00:00.000000

Adds ``subnets.owner_id`` (FK users, SET NULL).  The Site entity already
carries a per-site owner; this is the finer-grained "who manages THIS
subnet" handle the subnet-insights surface uses to make a neglected range
actionable.  Nullable with no backfill — existing subnets simply have no
owner until an operator assigns one.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'a7c3e91b5d28'
down_revision: Union[str, None] = 'b2e6d9f04a17'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("subnets") as batch:
        batch.add_column(sa.Column("owner_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_subnets_owner_id_users",
            "users",
            ["owner_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("subnets") as batch:
        batch.drop_constraint("fk_subnets_owner_id_users", type_="foreignkey")
        batch.drop_column("owner_id")
