"""hosts_v2.os_source: an operator's OS correction outlives the next scan; agents.is_active goes

Review 2026-10-23, H-AG2.  ``PATCH /agent/hosts/{id}`` set ``os_name`` and
``os_family``, and the next import replaced them whenever its OS guess was
more accurate than the stored one (an nmap ``-O`` match at 95 beat a
correction that carried no accuracy) — so a correction the agent guide says
"reads as settled" lasted until the next scan.  ``os_source`` is the OS
fields' provenance, as ``hostname_source`` is the display name's:
``'operator'`` when a person or their agent set the OS, NULL otherwise.  No
import replaces an operator's OS (the disagreement is still recorded as a
conflict); clearing the correction drops the lock.

Also review 2026-10-23 (agents nit): ``agents.is_active`` was never set false
by anything, so the auth chain's "agent inactive" refusal and the reactivation
at session start were dead and went with this release; the column goes with
them (legacy leaves with the change that orphans it).

**Upgrade**: adds the nullable column; drops ``agents.is_active``.  No other
data changes: an existing correction cannot be told from a scanner's value, so
earlier corrections stay unlocked until they are made again.
**Downgrade**: drops ``os_source`` (the locks are lost; the OS values stay) and
puts ``agents.is_active`` back NOT NULL, every row true (what it always held).

Revision ID: 33edf48e7db9
Revises: 47e5e79b90a9
"""
from alembic import op
import sqlalchemy as sa

revision = "33edf48e7db9"
down_revision = "47e5e79b90a9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("hosts_v2", sa.Column("os_source", sa.String(length=16), nullable=True))
    op.drop_column("agents", "is_active")


def downgrade() -> None:
    # As the baseline made it: NOT NULL with no server default (the old
    # model's default was Python-side), so the default only fills the rows.
    op.add_column(
        "agents", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.alter_column("agents", "is_active", server_default=None)
    op.drop_column("hosts_v2", "os_source")
