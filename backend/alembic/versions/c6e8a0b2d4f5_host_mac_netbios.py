"""hosts_v2: mac_address, mac_vendor, netbios_name (v2.390.0)

nmap's MAC address and vendor and Nessus's mac-address were read and dropped;
the NetBIOS name lived only in host_attributes, which nothing reads.  The
NetBIOS name is backfilled from the latest Nessus attribute per host.

Downgrade drops the columns.

Revision ID: c6e8a0b2d4f5
Revises: b5d7f9a1c3e4
Create Date: 2026-09-23
"""
import sqlalchemy as sa
from alembic import op


revision = "c6e8a0b2d4f5"
down_revision = "b5d7f9a1c3e4"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("hosts_v2", sa.Column("mac_address", sa.String(64), nullable=True))
    op.add_column("hosts_v2", sa.Column("mac_vendor", sa.String(128), nullable=True))
    op.add_column("hosts_v2", sa.Column("netbios_name", sa.String(64), nullable=True))
    op.execute(
        """
        UPDATE hosts_v2 h SET netbios_name = left(a.value, 64)
        FROM (
            SELECT DISTINCT ON (host_id) host_id, value
            FROM host_attributes
            WHERE attribute_type = 'netbios_name'
            ORDER BY host_id, id DESC
        ) a
        WHERE a.host_id = h.id
        """
    )


def downgrade():
    op.drop_column("hosts_v2", "netbios_name")
    op.drop_column("hosts_v2", "mac_vendor")
    op.drop_column("hosts_v2", "mac_address")
