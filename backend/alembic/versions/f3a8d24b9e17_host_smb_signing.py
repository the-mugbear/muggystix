"""add hosts_v2.smb_signing

Revision ID: f3a8d24b9e17
Revises: e2c5f8a1b934
Create Date: 2026-06-11 05:00:00.000000

SMB message-signing posture, extracted from nmap smb-security-mode /
netexec output to a queryable column (column-vs-blob policy) instead of
staying buried in HostScript output blobs.  Powers the systemic
"SMB signing disabled estate-wide" condition.  Nullable — populated as
the relevant scanner output is ingested; left NULL otherwise.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'f3a8d24b9e17'
down_revision: Union[str, None] = 'e2c5f8a1b934'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('hosts_v2', sa.Column('smb_signing', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('hosts_v2', 'smb_signing')
