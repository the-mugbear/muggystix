"""hosts_v2.os_source: an operator's OS correction outlives the next scan

Review 2026-10-23, H-AG2.  ``PATCH /agent/hosts/{id}`` set ``os_name`` and
``os_family``, and the next import replaced them whenever its OS guess was
more accurate than the stored one (an nmap ``-O`` match at 95 beat a
correction that carried no accuracy) — so a correction the agent guide says
"reads as settled" lasted until the next scan.  ``os_source`` is the OS
fields' provenance, as ``hostname_source`` is the display name's:
``'operator'`` when a person or their agent set the OS, NULL otherwise.  No
import replaces an operator's OS (the disagreement is still recorded as a
conflict); clearing the correction drops the lock.

**Upgrade**: adds the nullable column.  No data changes: an existing
correction cannot be told from a scanner's value, so earlier corrections stay
unlocked until they are made again.
**Downgrade**: drops the column (the locks are lost; the OS values stay).

Revision ID: 33edf48e7db9
Revises: c8e2a4f6b1d9
"""
from alembic import op
import sqlalchemy as sa

revision = "33edf48e7db9"
down_revision = "c8e2a4f6b1d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("hosts_v2", sa.Column("os_source", sa.String(length=16), nullable=True))


def downgrade() -> None:
    op.drop_column("hosts_v2", "os_source")
