"""host_scan_history.credentialed: did the scan authenticate to the host (review 2026-10-01 B12)

Revision ID: e5b8d2f4a7c9
Revises: d4a7c1e3f6b8
Create Date: 2026-10-01

Nessus says, per host, whether its local (authenticated) checks ran — the
``Credentialed_Scan`` host tag, or plugin 19506's ``Credentialed checks :
yes/no`` line.  It was discarded, so "a Nessus run that found nothing" read
the same whether or not the scanner ever logged in.

Nullable with no default: NULL means "the scan did not say", which is the
truth for every row written before this revision and for every tool that has
no such notion.  Nothing is backfilled — the files are not kept, and plugin
19506's text survives only on imports that did not skip informational items.
"""
import sqlalchemy as sa
from alembic import op

revision = "e5b8d2f4a7c9"
down_revision = "d4a7c1e3f6b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("host_scan_history", sa.Column("credentialed", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("host_scan_history", "credentialed")
