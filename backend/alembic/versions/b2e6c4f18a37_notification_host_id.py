"""notifications: add host_id for precise note deep-links

The Activity "Your Mentions" panel linked to /hosts?note=<id>, which the hosts
list ignores — a dead link. Carry the host on note/status notifications so the
panel can deep-link to /hosts/<host_id>#note-<source_id>. Backfills existing
note notifications from the annotation they reference.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b2e6c4f18a37"
down_revision: Union[str, None] = "a1d3f7c920e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("notifications", sa.Column("host_id", sa.Integer(), nullable=True))
    op.create_index("ix_notifications_host_id", "notifications", ["host_id"])
    # Backfill note-sourced notifications from the annotation they reference.
    op.execute(
        sa.text(
            """
            UPDATE notifications n
            SET host_id = a.host_id
            FROM annotations a
            WHERE n.source_type = 'note'
              AND n.source_id = a.id
              AND n.host_id IS NULL
            """
        )
    )


def downgrade() -> None:
    op.drop_index("ix_notifications_host_id", table_name="notifications")
    op.drop_column("notifications", "host_id")
