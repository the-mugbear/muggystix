"""web_interfaces unique on (scan_id, url, source, ip_address) (v2.416.0)

A web endpoint is its URL AND the address it was reached at.  Keyed by URL
alone, testssl had to build its URL from the IP (a name on two IPs collided),
which in turn collapsed two SNI names on one IP:port into one row and rolled
the second name's checks back.  Widening the key only relaxes it: no existing
row can violate the new constraint.

Revision ID: e5c9a3f7b2d8
Revises: d4b8e2a6f1c9
Create Date: 2026-09-25
"""
from alembic import op


revision = "e5c9a3f7b2d8"
down_revision = "d4b8e2a6f1c9"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("uq_web_interface_scan_url_source", "web_interfaces", type_="unique")
    op.create_unique_constraint(
        "uq_web_interface_scan_url_source_ip", "web_interfaces",
        ["scan_id", "url", "source", "ip_address"],
        postgresql_nulls_not_distinct=True,
    )


def downgrade():
    # Rows that differ only by address cannot survive the narrower key: keep
    # the lowest id of each (scan_id, url, source).
    op.execute(
        """
        DELETE FROM web_interfaces w USING web_interfaces k
        WHERE w.scan_id = k.scan_id AND w.url = k.url AND w.source = k.source
          AND w.id > k.id
        """
    )
    op.drop_constraint("uq_web_interface_scan_url_source_ip", "web_interfaces", type_="unique")
    op.create_unique_constraint(
        "uq_web_interface_scan_url_source", "web_interfaces", ["scan_id", "url", "source"],
    )
