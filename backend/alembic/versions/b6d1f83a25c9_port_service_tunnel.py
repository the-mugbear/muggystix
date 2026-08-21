"""add ports_v2.service_tunnel

nmap reports TLS as a separate attribute: what its text output prints as
``ssl/http`` is ``<service name="http" tunnel="ssl">`` in the XML.  The parser
kept ``name`` and dropped ``tunnel``, so an HTTPS service on a non-standard port
was stored identically to plaintext HTTP.

Found by an agent running the recon workflow end to end: nmap identified
``192.168.7.245:3000`` as ``ssl/http`` twice, and the derived web target came
back as ``http://192.168.7.245:3000/`` — because ``service_name`` was the bare
string ``http`` and the scheme heuristic had nothing else to go on.  Every
downstream consumer that asks "is this service TLS-wrapped?" had the same blind
spot; :3000 was simply where it became visible.

Backfill is not possible — the attribute was never stored, and the source XML
is not retained in a form this migration can read.  Existing rows stay NULL,
which reads as "unknown", and a re-scan populates them.

Revision ID: b6d1f83a25c9
Revises: f1a6c92d4b70
Create Date: 2026-08-21
"""
from alembic import op
import sqlalchemy as sa


revision = "b6d1f83a25c9"
down_revision = "f1a6c92d4b70"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ports_v2",
        sa.Column("service_tunnel", sa.String(length=20), nullable=True),
    )
    op.create_index(
        "ix_ports_v2_service_tunnel", "ports_v2", ["service_tunnel"],
    )


def downgrade() -> None:
    op.drop_index("ix_ports_v2_service_tunnel", table_name="ports_v2")
    op.drop_column("ports_v2", "service_tunnel")
