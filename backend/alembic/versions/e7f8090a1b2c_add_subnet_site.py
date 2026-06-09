"""add subnets.site

A free-text site/location attribute on each scope entry (subnet), e.g.
"London DC".  Nullable; populated from column 4 of the scope-upload CSV or
edited inline.  Purely additive.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e7f8090a1b2c"
down_revision: Union[str, None] = "d6e7f8090a1b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("subnets", sa.Column("site", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("subnets", "site")
