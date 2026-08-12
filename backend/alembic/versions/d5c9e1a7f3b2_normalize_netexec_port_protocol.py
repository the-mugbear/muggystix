"""normalize non-transport port protocols (netexec smb/ldap/winrm) to tcp

Revision ID: d5c9e1a7f3b2
Revises: c2e5a9f10b47
Create Date: 2026-08-12

The NetExec parser stored its NXC transport (smb / ldap / winrm / mssql / …) in
the port `protocol` column, but that column is the IP transport (tcp/udp) — the
NXC value is the *service*. Because the dedup key is (host, port_number,
protocol), a physical port (e.g. 445) recorded by NetExec as `445/smb` did NOT
collapse against the same port from an nmap `445/tcp` scan, producing a
duplicate open row that inflated `open_port_count` (used for triage on the
assist surface AND the /hosts page).

The parser is fixed to write `protocol='tcp'` with the NXC value as
`service_name`. This migration cleans the rows already written:

  1. Delete non-transport rows that duplicate an existing `tcp` row for the same
     physical (host, port_number) — they carry no vulns/web/scripts (verified),
     only cascade-deleting confidence/scan-history metadata.
  2. Collapse any (pathological) two non-transport rows for the same physical
     port to the lowest id, so step 3 can't violate uq_host_port_protocol.
  3. Relabel the remaining non-transport rows (the sole row for their port) to
     `protocol='tcp'`; the app-protocol stays in `service_name`.

Idempotent: after it runs there are no non-transport protocol rows, so a re-run
touches nothing. No downgrade — the pre-fix mislabeled rows are a bug, not a
state worth restoring.
"""
from alembic import op
import sqlalchemy as sa

revision = "d5c9e1a7f3b2"
down_revision = "c2e5a9f10b47"
branch_labels = None
depends_on = None

_NON_TRANSPORT = "lower(protocol) NOT IN ('tcp', 'udp') OR protocol IS NULL"


def upgrade() -> None:
    conn = op.get_bind()

    # 1. True duplicates: a tcp row already represents this physical port.
    conn.execute(sa.text(
        """
        DELETE FROM ports_v2 bad
        USING ports_v2 good
        WHERE (lower(bad.protocol) NOT IN ('tcp', 'udp') OR bad.protocol IS NULL)
          AND good.protocol = 'tcp'
          AND good.host_id = bad.host_id
          AND good.port_number = bad.port_number
        """
    ))

    # 2. Guard against two non-transport rows for one physical port (keep min id).
    conn.execute(sa.text(
        """
        DELETE FROM ports_v2 a
        USING ports_v2 b
        WHERE (lower(a.protocol) NOT IN ('tcp', 'udp') OR a.protocol IS NULL)
          AND (lower(b.protocol) NOT IN ('tcp', 'udp') OR b.protocol IS NULL)
          AND a.host_id = b.host_id
          AND a.port_number = b.port_number
          AND a.id > b.id
        """
    ))

    # 3. The rest are the sole row for their port — transport is tcp.
    conn.execute(sa.text(
        f"UPDATE ports_v2 SET protocol = 'tcp' WHERE {_NON_TRANSPORT}"
    ))


def downgrade() -> None:
    # One-way: the pre-fix mislabeled protocol values are a data bug.
    pass
