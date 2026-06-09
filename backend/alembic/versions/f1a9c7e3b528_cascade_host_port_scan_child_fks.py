"""cascade/set-null FKs that reference hosts_v2.id, ports_v2.id, scans.id

Revision ID: f1a9c7e3b528
Revises: 1a2b3c4d5e6f
Create Date: 2026-06-09 23:40:00.000000

Audit:  b7c2a09f1d44 made every ``projects.id`` FK cascade so the
delete-project endpoint would stop raising IntegrityError — but it
only fixed the *direct* project children.  Deleting a project cascades
into ``hosts_v2`` and ``scans``, and the grandchild tables that FK to
those two (plus ``ports_v2``) still had ``ON DELETE NO ACTION``.  So
``DELETE /projects/{id}`` (projects.py ``db.delete(project)``) still
hard-fails for any project that has been scanned (host_scan_history is
written for every host on every scan), and scan deletion only works
because of the 130-line runtime ``_clear_fk_refs`` pg_constraint
reflection workaround in scans.py.

This migration gives every remaining host/port/scan child FK an
explicit ``ON DELETE`` action:

  * CASCADE for OWNED rows — a row that is meaningless without its
    parent (a host's ports/vulns/attributes/confidence/history/scripts;
    a port's attributes/confidence/history/scripts; a scan's
    scan_info/out_of_scope_hosts/data_source_metadata and the per-scan
    confidence/attribute/history observations).  All of these columns
    are NOT NULL, so SET NULL is not an option anyway.

  * SET NULL for nullable AUDIT POINTERS — a row that outlives the
    referenced parent and only carries it as provenance
    (``*.last_updated_scan_id``, ``conflict_history.{previous,new}_scan_id``,
    ``ingestion_jobs.scan_id``, and ``vulnerabilities.port_id`` for
    host-level vulns; the vuln row itself still cascades via host_id /
    scan_id).

Constraints already on CASCADE/SET NULL (annotations, web_interfaces,
finding_hosts, host_sanity_checks, test_plan_entries,
host_tag_assignments, dns_records) are untouched.

Once this is deployed, the ``_clear_fk_refs`` workaround in scans.py
can be deleted (the DB cascade is now authoritative) — handled in a
follow-up so this migration stays a pure schema change.

Down-revision restores NO ACTION for symmetry; do not rely on rollback
semantics for cascade-FK changes on a populated DB.
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'f1a9c7e3b528'
down_revision: Union[str, None] = '1a2b3c4d5e6f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (fk_constraint_name, child_table, referenced_table, [local_col])
CASCADE_FKS = [
    # hosts_v2 children (host_id, all NOT NULL)
    ('host_attributes_host_id_fkey', 'host_attributes', 'hosts_v2', 'host_id'),
    ('host_confidence_host_id_fkey', 'host_confidence', 'hosts_v2', 'host_id'),
    ('host_follows_host_id_fkey', 'host_follows', 'hosts_v2', 'host_id'),
    ('host_scan_history_host_id_fkey', 'host_scan_history', 'hosts_v2', 'host_id'),
    ('host_scripts_v2_host_id_fkey', 'host_scripts_v2', 'hosts_v2', 'host_id'),
    ('host_subnet_mappings_host_id_fkey', 'host_subnet_mappings', 'hosts_v2', 'host_id'),
    ('netexec_results_host_id_fkey', 'netexec_results', 'hosts_v2', 'host_id'),
    ('ports_v2_host_id_fkey', 'ports_v2', 'hosts_v2', 'host_id'),
    ('vulnerabilities_host_id_fkey', 'vulnerabilities', 'hosts_v2', 'host_id'),
    # ports_v2 children (port_id, all NOT NULL)
    ('port_attributes_port_id_fkey', 'port_attributes', 'ports_v2', 'port_id'),
    ('port_confidence_port_id_fkey', 'port_confidence', 'ports_v2', 'port_id'),
    ('port_scan_history_port_id_fkey', 'port_scan_history', 'ports_v2', 'port_id'),
    ('scripts_v2_port_id_fkey', 'scripts_v2', 'ports_v2', 'port_id'),
    # scopes/subnets branch (the projects-cascade migration claimed scopes
    # "cascade through to subnets" but the FKs were actually NO ACTION).
    ('subnets_scope_id_fkey', 'subnets', 'scopes', 'scope_id'),
    ('host_subnet_mappings_subnet_id_fkey', 'host_subnet_mappings', 'subnets', 'subnet_id'),
    # scans children (scan_id, all NOT NULL — owned per-scan rows/observations)
    ('data_source_metadata_scan_id_fkey', 'data_source_metadata', 'scans', 'scan_id'),
    ('host_attributes_scan_id_fkey', 'host_attributes', 'scans', 'scan_id'),
    ('host_confidence_scan_id_fkey', 'host_confidence', 'scans', 'scan_id'),
    ('host_scan_history_scan_id_fkey', 'host_scan_history', 'scans', 'scan_id'),
    ('host_scripts_v2_scan_id_fkey', 'host_scripts_v2', 'scans', 'scan_id'),
    ('netexec_results_scan_id_fkey', 'netexec_results', 'scans', 'scan_id'),
    ('out_of_scope_hosts_scan_id_fkey', 'out_of_scope_hosts', 'scans', 'scan_id'),
    ('port_attributes_scan_id_fkey', 'port_attributes', 'scans', 'scan_id'),
    ('port_confidence_scan_id_fkey', 'port_confidence', 'scans', 'scan_id'),
    ('port_scan_history_scan_id_fkey', 'port_scan_history', 'scans', 'scan_id'),
    ('scan_info_scan_id_fkey', 'scan_info', 'scans', 'scan_id'),
    ('scripts_v2_scan_id_fkey', 'scripts_v2', 'scans', 'scan_id'),
    ('vulnerabilities_scan_id_fkey', 'vulnerabilities', 'scans', 'scan_id'),
]

# Nullable audit pointers — row outlives the referenced parent.
SET_NULL_FKS = [
    ('vulnerabilities_port_id_fkey', 'vulnerabilities', 'ports_v2', 'port_id'),
    ('conflict_history_new_scan_id_fkey', 'conflict_history', 'scans', 'new_scan_id'),
    ('conflict_history_previous_scan_id_fkey', 'conflict_history', 'scans', 'previous_scan_id'),
    ('hosts_v2_last_updated_scan_id_fkey', 'hosts_v2', 'scans', 'last_updated_scan_id'),
    ('ingestion_jobs_scan_id_fkey', 'ingestion_jobs', 'scans', 'scan_id'),
    ('ports_v2_last_updated_scan_id_fkey', 'ports_v2', 'scans', 'last_updated_scan_id'),
    # ingestion_jobs points at the parse_error it produced (both are project
    # children that cascade independently; null the pointer so neither delete
    # order blocks).
    ('ingestion_jobs_parse_error_id_fkey', 'ingestion_jobs', 'parse_errors', 'parse_error_id'),
    # annotation thread self-references — deleting a parent comment must not
    # block (or delete) its replies; the host/scan/port cascade owns the rows.
    ('host_notes_parent_id_fkey', 'annotations', 'annotations', 'parent_id'),
    ('fk_host_notes_thread_root_id', 'annotations', 'annotations', 'thread_root_id'),
]


def _rebuild(fk_name, table, ref_table, col, ondelete):
    op.drop_constraint(fk_name, table, type_='foreignkey')
    op.create_foreign_key(fk_name, table, ref_table, [col], ['id'], ondelete=ondelete)


def upgrade() -> None:
    for fk_name, table, ref_table, col in CASCADE_FKS:
        _rebuild(fk_name, table, ref_table, col, 'CASCADE')
    for fk_name, table, ref_table, col in SET_NULL_FKS:
        _rebuild(fk_name, table, ref_table, col, 'SET NULL')


def downgrade() -> None:
    for fk_name, table, ref_table, col in CASCADE_FKS + SET_NULL_FKS:
        _rebuild(fk_name, table, ref_table, col, 'NO ACTION')
