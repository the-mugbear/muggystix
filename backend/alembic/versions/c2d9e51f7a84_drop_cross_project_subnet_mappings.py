"""Delete host_subnet_mappings that cross a project boundary

v2.342.0 — the scan-time correlation path
(``SubnetCorrelationService.batch_correlate_scan_hosts_to_subnets``) built its
lookup trie from EVERY subnet in the database, not the scan's project, so a
host in project B could be mapped to a subnet declared in project A.  Scope
coverage is derived from these rows (a host with a mapping is "in scope"), so
the stray rows made hosts look in scope against a scope that was not theirs
and listed them among the other project's subnet hosts.

The service now resolves the scan's project before building the trie; this
migration removes the rows the old path already wrote.  Ingest of any scan
re-correlates its hosts, so nothing is lost that a re-correlate would not
recreate.  Irreversible by nature (the rows were wrong), so downgrade is a
no-op.

Revision ID: c2d9e51f7a84
Revises: b3c8e1f47a92
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "c2d9e51f7a84"
down_revision: Union[str, None] = "b3c8e1f47a92"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM host_subnet_mappings m
        USING hosts_v2 h, subnets s, scopes sc
        WHERE m.host_id = h.id
          AND m.subnet_id = s.id
          AND s.scope_id = sc.id
          AND h.project_id IS DISTINCT FROM sc.project_id
        """
    )


def downgrade() -> None:
    # The deleted rows were incorrect; there is nothing to restore.
    pass
