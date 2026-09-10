"""scans.time_source; port_scan_history.port_created + service_name

Two things /scans needs to tell the truth (v2.333.0):

1. ``scans.time_source`` — what a naive ``start_time``/``end_time`` means
   (``tool_run`` / ``tool_records`` = absolute, stored as UTC; ``tool_clock``
   = the scanner's zone-less wall clock, stored as written).  Before this the
   UI could not tell a scanner timestamp from the upload time that
   ``parser_utils.ensure_scan`` stamped into ``start_time`` for tools whose
   output carries no time, and rendered every naive value in the viewer's
   zone as if it were local.

   Backfill, by what each parser has always written:
     * nmap XML (``scan_type='nmap'``) and masscan XML (the only masscan
       format that set a start) read epoch seconds -> ``tool_run``.
     * gnmap (``*_gnmap``) parsed "scan initiated <ctime>" with no zone ->
       ``tool_clock``.
     * The ensure_scan tools (their start_time is the upload instant, within
       seconds of created_at, and they never set end_time) -> start_time is
       cleared.  created_at still holds that instant, so nothing is lost;
       downgrade restores it from created_at.
     * Anything else keeps a NULL source: a legacy value of unknown origin.

2. ``port_scan_history.port_created`` (mirrors ``host_created``: this scan
   created the port row) and ``port_scan_history.service_name`` (the name
   already inside the ``service_info`` JSON, promoted so a per-scan
   aggregate doesn't parse a blob — CLAUDE.md column-vs-blob policy).
   Backfill: the earliest observation of each port is its creator; the name
   is copied out of the JSON the dedup service wrote.

Revision ID: c3e9a7d15b62
Revises: b8d4e6f1a903
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3e9a7d15b62"
down_revision: Union[str, None] = "b8d4e6f1a903"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Parsers that went through parser_utils.ensure_scan and had no tool time.
_UPLOAD_STAMPED_TOOLS = (
    "rustscan", "naabu", "openvas", "nikto", "dnsx", "amass", "subfinder",
    "dirbuster", "gobuster", "feroxbuster", "ffuf", "dirsearch",
    "smbmap", "bloodhound",
)


def upgrade() -> None:
    op.add_column("scans", sa.Column("time_source", sa.String(16), nullable=True))
    op.add_column(
        "port_scan_history",
        sa.Column("port_created", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("port_scan_history", sa.Column("service_name", sa.String(), nullable=True))

    op.execute(sa.text(
        """
        UPDATE scans SET time_source = 'tool_run'
        WHERE start_time IS NOT NULL
          AND (scan_type = 'nmap'
               OR (lower(tool_name) = 'masscan' AND scan_type = 'port_scan'))
        """
    ))
    op.execute(sa.text(
        """
        UPDATE scans SET time_source = 'tool_clock'
        WHERE start_time IS NOT NULL AND scan_type IN ('nmap_gnmap', 'masscan_gnmap')
        """
    ))
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            UPDATE scans SET start_time = NULL
            WHERE time_source IS NULL
              AND start_time IS NOT NULL
              AND end_time IS NULL
              AND lower(tool_name) IN :tools
              AND abs(extract(epoch FROM (start_time AT TIME ZONE 'UTC') - created_at)) < 120
            """
        ).bindparams(sa.bindparam("tools", expanding=True)),
        {"tools": list(_UPLOAD_STAMPED_TOOLS)},
    )

    # First observation of each port created it (same rule a1d3f7c920e4 used
    # for host_created).
    op.execute(sa.text(
        """
        UPDATE port_scan_history
        SET port_created = true
        WHERE id IN (
            SELECT DISTINCT ON (port_id) id
            FROM port_scan_history
            ORDER BY port_id, discovered_at ASC NULLS FIRST, id ASC
        )
        """
    ))
    # service_info is only ever written by json.dumps (dedup service); the
    # object guard keeps a hand-edited row from failing the whole migration.
    op.execute(sa.text(
        """
        UPDATE port_scan_history
        SET service_name = NULLIF(service_info::json ->> 'service_name', '')
        WHERE service_info IS NOT NULL
          AND left(ltrim(service_info), 1) = '{'
        """
    ))


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            UPDATE scans SET start_time = created_at AT TIME ZONE 'UTC'
            WHERE start_time IS NULL AND lower(tool_name) IN :tools
            """
        ).bindparams(sa.bindparam("tools", expanding=True)),
        {"tools": list(_UPLOAD_STAMPED_TOOLS)},
    )
    op.drop_column("port_scan_history", "service_name")
    op.drop_column("port_scan_history", "port_created")
    op.drop_column("scans", "time_source")
