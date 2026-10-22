"""drop the indexes another index already provides

Review 2026-10-10, R8.  Every write maintains every index of its table, and a
Nessus re-import writes a row per finding: ``vulnerabilities`` carried 18
indexes, ``hosts_v2`` two identical ``btree (ip_address)``.  This revision drops
the 100 single-column btree indexes that are REDUNDANT — each one is either

* ``ix_<table>_id``, a second index on the primary key (``primary_key=True,
  index=True`` on the model — 54 tables), or
* a left prefix of another, non-partial btree index or unique constraint of
  the same table (``ix_hosts_v2_ip_address`` duplicates ``idx_host_ip_address``;
  ``ix_hosts_v2_project_id`` leads ``uq_project_ip``; ``ix_vulnerabilities_host_id``
  leads ``idx_vulnerability_host_severity``; and so on — the covering index is
  named beside each below).

Every lookup one of them served is served by the index that covers it; no
``ON CONFLICT`` target names an index (they name columns, and every unique
constraint stays), every foreign key into a bulk-deleted table keeps an index
led by its column (``tests/test_fk_child_index_contract.py``), and
``tests/test_redundant_indexes.py`` keeps a new one from coming back.

**Upgrade**: drops the indexes.  No data changes.
**Downgrade**: recreates them as they were.

Revision ID: a5d9c3e1f7b2
Revises: f4c8b2d0e6a1
"""
from alembic import op

revision = "a5d9c3e1f7b2"
down_revision = "f4c8b2d0e6a1"
branch_labels = None
depends_on = None

#: Tables whose ``ix_<table>_id`` duplicates their primary key.
PRIMARY_KEY_DUPLICATES = (
    "activity_cursors", "agent_feedback", "agent_proposals", "agents", "annotations",
    "api_keys", "audit_logs", "conflict_history", "dns_names", "dns_records",
    "evidence_records", "finding_vulnerabilities", "host_attributes", "host_confidence",
    "host_filter_views", "host_follows", "host_network_attributions", "host_scan_history",
    "host_scripts_v2", "host_subnet_mappings", "hosts_v2", "ingestion_jobs",
    "integration_credentials", "llm_providers", "netexec_results", "network_attributions",
    "note_mentions", "notifications", "operations_cursors", "parse_errors", "port_confidence",
    "port_scan_history", "ports_v2", "project_memberships", "projects", "query_history",
    "report_files", "report_images", "report_profiles", "reports", "saved_queries",
    "scan_batches", "scan_info", "scans", "scope_domains", "scopes", "scripts_v2", "subnets",
    "user_sessions", "users", "vulnerabilities", "web_interfaces", "web_paths",
    "webhook_deliveries",
)

#: (index, table, column) — each the left prefix of the index named after it.
LEFT_PREFIXES = (
    ("ix_activity_cursors_user_id", "activity_cursors", "user_id"),  # uq_activity_cursor_user_project
    ("ix_agent_api_calls_agent_id", "agent_api_calls", "agent_id"),  # idx_agent_api_call_agent_created
    ("ix_agent_api_calls_project_id", "agent_api_calls", "project_id"),  # idx_agent_api_call_project_created
    ("ix_agent_proposals_project_id", "agent_proposals", "project_id"),  # idx_agent_proposals_project_status
    ("ix_agents_project_id", "agents", "project_id"),  # uq_agent_per_user_project
    ("ix_dns_names_project_id", "dns_names", "project_id"),  # uq_dns_name_project_fqdn
    ("ix_dns_records_project_id", "dns_records", "project_id"),  # idx_dns_record_project_value
    ("ix_evidence_records_project_id", "evidence_records", "project_id"),  # uq_evidence_request
    ("ix_finding_hosts_finding_id", "finding_hosts", "finding_id"),  # uq_finding_host_name
    ("ix_finding_vulnerabilities_finding_id", "finding_vulnerabilities", "finding_id"),  # uq_finding_vulnerability
    ("ix_findings_project_id", "findings", "project_id"),  # idx_finding_project_status
    ("ix_host_attributes_host_id", "host_attributes", "host_id"),  # uq_host_attribute_key
    ("ix_host_filter_views_user_id", "host_filter_views", "user_id"),  # uq_host_filter_view_name
    ("ix_host_network_attributions_host_id", "host_network_attributions", "host_id"),  # uq_host_attribution
    ("ix_host_subnet_mappings_host_id", "host_subnet_mappings", "host_id"),  # uq_host_subnet
    ("ix_host_tag_assignments_host_id", "host_tag_assignments", "host_id"),  # uq_host_tag_assignment
    ("ix_host_tags_project_id", "host_tags", "project_id"),  # uq_host_tag_name
    ("ix_hosts_v2_ip_address", "hosts_v2", "ip_address"),  # idx_host_ip_address (identical)
    ("ix_hosts_v2_project_id", "hosts_v2", "project_id"),  # uq_project_ip
    ("ix_llm_providers_user_id", "llm_providers", "user_id"),  # uq_llm_provider_user_name
    ("ix_mcp_tool_calls_outcome", "mcp_tool_calls", "outcome"),  # idx_mcp_tool_call_outcome_created
    ("ix_mcp_tool_calls_tool_name", "mcp_tool_calls", "tool_name"),  # idx_mcp_tool_call_tool_created
    ("ix_network_attributions_project_id", "network_attributions", "project_id"),  # uq_attribution_block
    ("ix_operations_cursors_user_id", "operations_cursors", "user_id"),  # uq_operations_cursor_user_project
    ("idx_port_scan_history_scan", "port_scan_history", "scan_id"),  # idx_port_scan_history_scan_state
    ("ix_ports_v2_port_number", "ports_v2", "port_number"),  # idx_port_number_protocol
    ("ix_project_memberships_project_id", "project_memberships", "project_id"),  # uq_project_user
    ("ix_query_history_user_id", "query_history", "user_id"),  # ix_query_history_user_project_kind_last
    ("ix_report_files_report_id", "report_files", "report_id"),  # uq_report_file_format
    ("ix_report_images_report_id", "report_images", "report_id"),  # uq_report_image_attachment
    ("ix_report_jobs_project_id", "report_jobs", "project_id"),  # idx_report_jobs_project_status
    ("ix_reports_project_id", "reports", "project_id"),  # uq_report_project_number
    ("ix_saved_queries_project_id", "saved_queries", "project_id"),  # uq_saved_query_name
    ("ix_scope_domains_scope_id", "scope_domains", "scope_id"),  # uq_scope_domain
    ("ix_scopes_project_id", "scopes", "project_id"),  # uq_scopes_project (identical)
    ("ix_sites_project_id", "sites", "project_id"),  # uq_site_project_name
    ("ix_subnet_label_assignments_subnet_id", "subnet_label_assignments", "subnet_id"),  # uq_subnet_label_assignment
    ("ix_subnet_labels_project_id", "subnet_labels", "project_id"),  # uq_subnet_label_name
    ("ix_tool_registry_name", "tool_registry", "name"),  # uq_tool_registry_name (identical)
    ("ix_tool_registry_status", "tool_registry", "status"),  # idx_tool_registry_status_name
    ("ix_vulnerabilities_host_id", "vulnerabilities", "host_id"),  # idx_vulnerability_host_severity
    ("ix_web_interfaces_favicon_hash", "web_interfaces", "favicon_hash"),  # idx_web_interface_favicon
    ("ix_web_interfaces_host_id", "web_interfaces", "host_id"),  # idx_web_interface_host
    ("ix_web_interfaces_scan_id", "web_interfaces", "scan_id"),  # uq_web_interface_scan_url_source_ip
    ("ix_web_paths_scan_id", "web_paths", "scan_id"),  # uq_web_paths_scan_url
    ("ix_webhook_deliveries_status", "webhook_deliveries", "status"),  # idx_webhook_delivery_due
)


def dropped() -> tuple:
    """Every (index, table, column) this revision drops."""
    return tuple((f"ix_{t}_id", t, "id") for t in PRIMARY_KEY_DUPLICATES) + LEFT_PREFIXES


def upgrade() -> None:
    for name, table, _column in dropped():
        op.drop_index(name, table_name=table)


def downgrade() -> None:
    for name, table, column in dropped():
        op.create_index(name, table, [column])
