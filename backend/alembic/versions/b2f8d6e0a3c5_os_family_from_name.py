"""Fill hosts_v2.os_family from os_name where no scanner gave one (v2.421.0)

Only nmap supplied an OS family; Nessus, NetExec and operator corrections give
a name only, so most hosts on a Nessus-heavy inventory had a name and no
family (production: 43,358 of 51,132).  New imports derive it
(app.services.os_family); this fills the hosts already stored.  Only blanks
are filled — a scanner's family is never replaced — one UPDATE per distinct
name, so the cost follows the number of distinct OS strings, not hosts.

Revision ID: b2f8d6e0a3c5
Revises: a1e7c5b9d2f4
Create Date: 2026-09-26
"""
import sqlalchemy as sa
from alembic import op


revision = "b2f8d6e0a3c5"
down_revision = "a1e7c5b9d2f4"
branch_labels = None
depends_on = None


def upgrade():
    from app.services.os_family import os_family_from_name

    bind = op.get_bind()
    names = [
        row[0] for row in bind.execute(sa.text(
            "SELECT DISTINCT os_name FROM hosts_v2 WHERE os_family IS NULL AND os_name IS NOT NULL"
        ))
    ]
    for name in names:
        family = os_family_from_name(name)
        if family:
            bind.execute(
                sa.text("UPDATE hosts_v2 SET os_family = :family "
                        "WHERE os_family IS NULL AND os_name = :name"),
                {"family": family, "name": name},
            )


def downgrade():
    # A derived family cannot be told from a scanned one once stored, and a
    # filled blank is harmless to the previous code: nothing to undo.
    pass
