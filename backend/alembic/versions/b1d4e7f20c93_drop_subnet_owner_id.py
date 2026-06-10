"""drop subnets.owner_id — subnet ownership is not a product requirement

Revision ID: b1d4e7f20c93
Revises: f3b8c1d92a4e
Create Date: 2026-06-10 00:00:00.000000

``subnets.owner_id`` was added (a7c3e91b5d28) to make a neglected subnet
"actionable" in the insights view, but per-subnet ownership turned out not
to be something this app needs — the column was never assignable and always
read "unassigned".  Reverts the dead column + its FK.  (The separate, used
Site.owner is untouched.)
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'b1d4e7f20c93'
down_revision: Union[str, None] = 'f3b8c1d92a4e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("subnets") as batch:
        batch.drop_constraint("fk_subnets_owner_id_users", type_="foreignkey")
        batch.drop_column("owner_id")


def downgrade() -> None:
    with op.batch_alter_table("subnets") as batch:
        batch.add_column(sa.Column("owner_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_subnets_owner_id_users", "users", ["owner_id"], ["id"], ondelete="SET NULL",
        )
