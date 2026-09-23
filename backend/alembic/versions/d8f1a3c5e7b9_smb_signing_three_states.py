"""hosts_v2.smb_signing: one vocabulary for nmap and NetExec (v2.387.0)

``enabled`` meant opposite things by tool: nmap wrote it for "enabled but not
required", NetExec for ``(signing:True)`` — required.  NetExec also wrote
``disabled`` for ``(signing:False)``, which is "not required".  The states are
now required / not_required / disabled (``app.services.smb_signing``).

Data migration:
* ``enabled`` on a host whose NetExec output said ``signing:True`` → required;
* every other ``enabled`` (nmap's meaning) → not_required.
``disabled`` rows are left alone: NetExec's "not required" cannot be told from
a real SMB1 "disabled" after the fact, and both are relayable.

Downgrade maps not_required back to ``enabled``.

Revision ID: d8f1a3c5e7b9
Revises: c2e8a4f6b1d3
Create Date: 2026-09-23
"""
from alembic import op


revision = "d8f1a3c5e7b9"
down_revision = "c2e8a4f6b1d3"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        UPDATE hosts_v2 SET smb_signing = 'required'
        WHERE smb_signing = 'enabled'
          AND EXISTS (
            SELECT 1 FROM netexec_results nr
            WHERE nr.host_id = hosts_v2.id AND lower(nr.raw_output) LIKE '%signing:true%'
          )
        """
    )
    op.execute("UPDATE hosts_v2 SET smb_signing = 'not_required' WHERE smb_signing = 'enabled'")


def downgrade():
    op.execute("UPDATE hosts_v2 SET smb_signing = 'enabled' WHERE smb_signing = 'not_required'")
