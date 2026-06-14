"""host_follows: add review_conclusion + review_summary (§9 review completion)

Marking a host Reviewed now records WHAT the reviewer concluded (no actionable
issue / finding created / needs more evidence / out of scope / duplicate) plus
an optional summary, so "reviewed" is an auditable outcome rather than a bare
bookmark. Both nullable; no backfill (pre-existing reviews simply have no
recorded conclusion).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c5f9a2d83b41"
down_revision: Union[str, None] = "b2e6c4f18a37"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("host_follows", sa.Column("review_conclusion", sa.String(length=32), nullable=True))
    op.add_column("host_follows", sa.Column("review_summary", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("host_follows", "review_summary")
    op.drop_column("host_follows", "review_conclusion")
