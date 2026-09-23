"""Repair data two parser bugs wrote (review 2026-09-23 C6a, C6e) (v2.391.0)

Re-importing does not undo either, so the rows are corrected here:

1. **nmap web interfaces on non-web TLS ports.**  From v2.390.0 nmap's
   ``ssl-cert`` / ``ssl-enum-ciphers`` wrote a ``web_interfaces`` row for any
   port that ran them — RDP 3389, LDAPS, SMTPS, MSSQL — so a Windows host read
   as ``has:web`` and, RDP certificates being self-signed, ``has:cert_issue``.
   Deleted: rows with ``source = 'nmap'`` whose port is not a web service.
   ``winrm`` / ``wsman`` count as web: NetExec's name overwrote nmap's
   ``http`` on WinRM ports before v2.390.3, and 5986 IS an HTTPS listener.
   A row with no port is left alone (nothing to judge it by).

2. **An address stored as a host's name.**  Nikto run against an IP reported
   the IP as its host and it became ``hosts_v2.hostname``, blocking real
   names of equal rank.  Cleared where the name equals the host's own
   address, unless an operator typed it.

Downgrade is a no-op: the deleted rows were wrong, and nothing can tell a
cleared IP-name from one that never existed.

Revision ID: f4b6d8a0c2e3
Revises: e2a4c6f8b0d1
Create Date: 2026-09-23
"""
from alembic import op


revision = "f4b6d8a0c2e3"
down_revision = "e2a4c6f8b0d1"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        DELETE FROM web_interfaces w
        USING ports_v2 p
        WHERE w.port_id = p.id
          AND w.source = 'nmap'
          AND NOT (
              lower(coalesce(p.service_name, '')) LIKE 'http%'
              OR lower(coalesce(p.service_name, '')) IN ('winrm', 'wsman', 'wsmans')
          )
        """
    )
    op.execute(
        """
        UPDATE hosts_v2
        SET hostname = NULL, hostname_source = NULL
        WHERE hostname = ip_address
          AND coalesce(hostname_source, '') <> 'operator'
        """
    )


def downgrade():
    pass
