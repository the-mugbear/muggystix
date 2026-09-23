"""netexec_results: tool, local_admin, smbv1; drop two constant columns (v2.390.0)

* ``tool`` — SMBMap's shares are stored beside NetExec's (they were dropped:
  the SMBMap parser kept only "445/tcp open").
* ``local_admin`` — NetExec's "(Pwn3d!)".
* ``smbv1`` — NetExec's "(SMBv1:True|False)" from the SMB banner.
* ``connection_stable`` / ``multiple_confirmations`` were written as constants
  and read by nothing.

Downgrade restores the two columns (as their old defaults) and drops the three.

Revision ID: a4c6e8f0b2d3
Revises: f3b5d7e9a1c2
Create Date: 2026-09-23
"""
import sqlalchemy as sa
from alembic import op


revision = "a4c6e8f0b2d3"
down_revision = "f3b5d7e9a1c2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("netexec_results", sa.Column("tool", sa.String(20), nullable=False, server_default="netexec"))
    op.add_column("netexec_results", sa.Column("local_admin", sa.Boolean(), nullable=True))
    op.add_column("netexec_results", sa.Column("smbv1", sa.Boolean(), nullable=True))
    # Backfill from the stored lines.
    op.execute("UPDATE netexec_results SET local_admin = true WHERE raw_output LIKE '%(Pwn3d!)%'")
    op.execute("UPDATE netexec_results SET smbv1 = true WHERE raw_output ILIKE '%(SMBv1:True)%'")
    op.execute("UPDATE netexec_results SET smbv1 = false WHERE raw_output ILIKE '%(SMBv1:False)%'")
    op.drop_column("netexec_results", "connection_stable")
    op.drop_column("netexec_results", "multiple_confirmations")


def downgrade():
    op.add_column("netexec_results", sa.Column("connection_stable", sa.Boolean(), nullable=True))
    op.add_column("netexec_results", sa.Column("multiple_confirmations", sa.Boolean(), nullable=True))
    op.execute("UPDATE netexec_results SET connection_stable = true, multiple_confirmations = false")
    op.execute("DELETE FROM netexec_results WHERE tool = 'smbmap'")
    op.drop_column("netexec_results", "smbv1")
    op.drop_column("netexec_results", "local_admin")
    op.drop_column("netexec_results", "tool")
