"""Guard against model/DB foreign-key ON DELETE drift.

Two migrations set the authoritative delete behaviour at the database level:
``b7c2a09f1d44`` (every ``projects.id`` child → CASCADE) and
``f1a9c7e3b528`` (host/port/scan child rows → CASCADE for owned rows, SET NULL
for nullable audit pointers).  But the test schema is built from the MODELS via
``Base.metadata.create_all()`` (see conftest), while production is built by
Alembic.  So when a model ``ForeignKey`` omits ``ondelete=``, the model-built
schema gets ``NO ACTION`` while Alembic-built prod has ``CASCADE``/``SET NULL`` —
the two diverge on delete semantics, and a project/scan delete that cascades in
prod can raise IntegrityError under a model-built schema (or vice-versa).

``EXPECTED_ONDELETE`` below is ground truth captured from the live Alembic-built
Postgres database.  This test asserts every model FK declares the matching
``ondelete`` so the drift can never silently reopen.  When you add or change an
FK, update this map in the same commit — that is the point of the contract.
"""
from app.db import models  # noqa: F401 — registers the core tables on Base
# Side-effect imports so every model module's tables land on Base.metadata
# before we walk it (mirrors conftest's registration list, plus confidence).
from app.db import (  # noqa: F401
    models_agent,
    models_auth,
    models_confidence,
    models_findings,
    models_integrations,
    models_llm,
    models_project,
    models_vulnerability,
)

# (child_table, child_column) -> ON DELETE rule, captured from the live
# Alembic-built Postgres schema (pg_constraint.confdeltype).
EXPECTED_ONDELETE = {
    ('activity_cursors', 'project_id'): 'CASCADE',
    ('activity_cursors', 'user_id'): 'CASCADE',
    ('agent_api_calls', 'agent_id'): 'CASCADE',
    ('agent_api_calls', 'api_key_id'): 'SET NULL',
    ('agent_api_calls', 'assist_session_id'): 'SET NULL',
    ('agent_api_calls', 'execution_session_id'): 'SET NULL',
    ('agent_api_calls', 'project_id'): 'CASCADE',
    ('agent_api_calls', 'recon_session_id'): 'SET NULL',
    ('agent_api_calls', 'scope_id'): 'SET NULL',
    ('agent_api_calls', 'test_plan_id'): 'SET NULL',
    ('agent_feedback', 'agent_id'): 'SET NULL',
    ('agent_feedback', 'assist_session_id'): 'SET NULL',
    ('agent_feedback', 'execution_session_id'): 'SET NULL',
    ('agent_feedback', 'project_id'): 'SET NULL',
    ('agent_feedback', 'recon_session_id'): 'SET NULL',
    ('agent_feedback', 'reviewed_by_id'): 'SET NULL',
    ('agent_feedback', 'test_plan_id'): 'SET NULL',
    ('agents', 'owner_id'): 'CASCADE',
    ('agents', 'project_id'): 'CASCADE',
    ('agent_sessions', 'agent_id'): 'SET NULL',
    ('agent_sessions', 'environment_probed_by_user_id'): 'SET NULL',
    ('agent_sessions', 'plan_id'): 'CASCADE',
    ('agent_sessions', 'project_id'): 'CASCADE',
    ('agent_sessions', 'scope_id'): 'CASCADE',
    ('agent_sessions', 'started_by_id'): 'SET NULL',
    ('annotations', 'assignee_id'): 'SET NULL',
    ('annotations', 'host_id'): 'CASCADE',
    ('annotations', 'parent_id'): 'SET NULL',
    ('annotations', 'plan_id'): 'CASCADE',
    ('annotations', 'port_id'): 'CASCADE',
    ('annotations', 'project_id'): 'CASCADE',
    ('annotations', 'scan_id'): 'CASCADE',
    ('annotations', 'scope_id'): 'CASCADE',
    ('annotations', 'thread_root_id'): 'SET NULL',
    ('annotations', 'user_id'): 'SET NULL',
    ('annotation_status_history', 'changed_by_id'): 'SET NULL',
    ('annotation_status_history', 'note_id'): 'CASCADE',
    ('api_keys', 'agent_id'): 'CASCADE',
    ('api_keys', 'agent_session_id'): 'CASCADE',
    ('api_keys', 'assist_session_id'): 'CASCADE',
    ('api_keys', 'recon_session_id'): 'CASCADE',
    ('api_keys', 'scope_id'): 'CASCADE',
    ('api_keys', 'test_plan_id'): 'CASCADE',
    ('api_keys', 'user_id'): 'CASCADE',
    ('assist_sessions', 'agent_id'): 'SET NULL',
    ('assist_sessions', 'agent_session_id'): 'CASCADE',
    ('assist_sessions', 'environment_probed_by_user_id'): 'SET NULL',
    ('assist_sessions', 'project_id'): 'CASCADE',
    ('assist_sessions', 'started_by_id'): 'SET NULL',
    ('audit_logs', 'user_id'): 'SET NULL',
    ('conflict_history', 'host_id'): 'CASCADE',
    ('conflict_history', 'new_scan_id'): 'SET NULL',
    ('conflict_history', 'port_id'): 'CASCADE',
    ('conflict_history', 'previous_scan_id'): 'SET NULL',
    ('dns_records', 'project_id'): 'CASCADE',
    ('dns_records', 'scan_id'): 'SET NULL',
    ('execution_sessions', 'agent_id'): 'SET NULL',
    ('execution_sessions', 'agent_session_id'): 'CASCADE',
    ('execution_sessions', 'environment_probed_by_user_id'): 'SET NULL',
    ('execution_sessions', 'started_by_id'): 'SET NULL',
    ('execution_sessions', 'test_plan_id'): 'CASCADE',
    ('finding_hosts', 'finding_id'): 'CASCADE',
    ('finding_hosts', 'host_id'): 'CASCADE',
    ('finding_hosts', 'port_id'): 'SET NULL',
    ('findings', 'created_by_id'): 'SET NULL',
    ('findings', 'evidence_annotation_id'): 'SET NULL',
    ('findings', 'exec_result_id'): 'CASCADE',
    ('findings', 'owner_id'): 'SET NULL',
    ('findings', 'project_id'): 'CASCADE',
    ('findings', 'vuln_id'): 'CASCADE',
    ('finding_status_history', 'changed_by_id'): 'SET NULL',
    ('finding_status_history', 'finding_id'): 'CASCADE',
    ('host_attributes', 'host_id'): 'CASCADE',
    ('host_attributes', 'scan_id'): 'CASCADE',
    ('host_confidence', 'host_id'): 'CASCADE',
    ('host_confidence', 'scan_id'): 'CASCADE',
    ('host_filter_views', 'project_id'): 'CASCADE',
    ('host_filter_views', 'user_id'): 'CASCADE',
    ('host_follows', 'assigned_by_id'): 'SET NULL',
    ('host_follows', 'host_id'): 'CASCADE',
    ('host_follows', 'user_id'): 'CASCADE',
    ('host_query_history', 'project_id'): 'CASCADE',
    ('host_query_history', 'user_id'): 'CASCADE',
    ('host_sanity_checks', 'entry_id'): 'CASCADE',
    ('host_sanity_checks', 'execution_session_id'): 'CASCADE',
    ('host_sanity_checks', 'host_id'): 'CASCADE',
    ('host_scan_history', 'host_id'): 'CASCADE',
    ('host_scan_history', 'scan_id'): 'CASCADE',
    ('host_scripts_v2', 'host_id'): 'CASCADE',
    ('host_scripts_v2', 'scan_id'): 'CASCADE',
    ('host_subnet_mappings', 'host_id'): 'CASCADE',
    ('host_subnet_mappings', 'subnet_id'): 'CASCADE',
    ('hosts_v2', 'last_updated_scan_id'): 'SET NULL',
    ('hosts_v2', 'project_id'): 'CASCADE',
    ('host_tag_assignments', 'created_by_id'): 'SET NULL',
    ('host_tag_assignments', 'host_id'): 'CASCADE',
    ('host_tag_assignments', 'tag_id'): 'CASCADE',
    ('host_tags', 'created_by_id'): 'SET NULL',
    ('host_tags', 'project_id'): 'CASCADE',
    ('imported_result_files', 'execution_session_id'): 'CASCADE',
    ('imported_result_files', 'imported_by_id'): 'SET NULL',
    ('imported_result_files', 'test_plan_id'): 'CASCADE',
    ('ingestion_jobs', 'parse_error_id'): 'SET NULL',
    ('ingestion_jobs', 'project_id'): 'CASCADE',
    ('ingestion_jobs', 'recon_session_id'): 'SET NULL',
    ('ingestion_jobs', 'scan_id'): 'SET NULL',
    ('ingestion_jobs', 'submitted_by_id'): 'SET NULL',
    ('integration_credentials', 'project_id'): 'CASCADE',
    ('integration_credentials', 'user_id'): 'CASCADE',
    ('llm_providers', 'user_id'): 'CASCADE',
    ('netexec_results', 'host_id'): 'CASCADE',
    ('netexec_results', 'scan_id'): 'CASCADE',
    ('note_attachments', 'annotation_id'): 'CASCADE',
    ('note_attachments', 'project_id'): 'CASCADE',
    ('note_attachments', 'uploaded_by_id'): 'SET NULL',
    ('note_mentions', 'note_id'): 'CASCADE',
    ('note_mentions', 'user_id'): 'CASCADE',
    ('notifications', 'actor_id'): 'SET NULL',
    ('notifications', 'project_id'): 'CASCADE',
    ('notifications', 'user_id'): 'CASCADE',
    ('operations_cursors', 'project_id'): 'CASCADE',
    ('operations_cursors', 'user_id'): 'CASCADE',
    ('out_of_scope_hosts', 'project_id'): 'CASCADE',
    ('out_of_scope_hosts', 'scan_id'): 'CASCADE',
    ('parse_errors', 'project_id'): 'CASCADE',
    ('port_confidence', 'port_id'): 'CASCADE',
    ('port_confidence', 'scan_id'): 'CASCADE',
    ('port_scan_history', 'port_id'): 'CASCADE',
    ('port_scan_history', 'scan_id'): 'CASCADE',
    ('ports_v2', 'host_id'): 'CASCADE',
    ('ports_v2', 'last_updated_scan_id'): 'SET NULL',
    ('project_memberships', 'project_id'): 'CASCADE',
    ('project_memberships', 'user_id'): 'CASCADE',
    ('projects', 'created_by_id'): 'SET NULL',
    ('recon_sessions', 'agent_id'): 'SET NULL',
    ('recon_sessions', 'agent_session_id'): 'CASCADE',
    ('recon_sessions', 'environment_probed_by_user_id'): 'SET NULL',
    ('recon_sessions', 'project_id'): 'CASCADE',
    ('recon_sessions', 'scope_id'): 'CASCADE',
    ('recon_sessions', 'started_by_id'): 'SET NULL',
    ('scan_info', 'scan_id'): 'CASCADE',
    ('scans', 'project_id'): 'CASCADE',
    ('scans', 'uploaded_by_id'): 'SET NULL',
    ('scopes', 'project_id'): 'CASCADE',
    ('scopes', 'uploaded_by_id'): 'SET NULL',
    ('scripts_v2', 'port_id'): 'CASCADE',
    ('scripts_v2', 'scan_id'): 'CASCADE',
    ('security_policies', 'updated_by_id'): 'SET NULL',
    ('sites', 'created_by_id'): 'SET NULL',
    ('sites', 'owner_id'): 'SET NULL',
    ('sites', 'project_id'): 'CASCADE',
    ('subnet_label_assignments', 'created_by_id'): 'SET NULL',
    ('subnet_label_assignments', 'label_id'): 'CASCADE',
    ('subnet_label_assignments', 'subnet_id'): 'CASCADE',
    ('subnet_labels', 'created_by_id'): 'SET NULL',
    ('subnet_labels', 'project_id'): 'CASCADE',
    ('subnets', 'scope_id'): 'CASCADE',
    ('subnets', 'site_id'): 'SET NULL',
    ('test_execution_results', 'entry_id'): 'CASCADE',
    ('test_execution_results', 'execution_session_id'): 'CASCADE',
    ('test_plan_entries', 'assigned_to_id'): 'SET NULL',
    ('test_plan_entries', 'host_id'): 'CASCADE',
    ('test_plan_entries', 'test_plan_id'): 'CASCADE',
    ('test_plan_history', 'entry_id'): 'SET NULL',
    ('test_plan_history', 'test_plan_id'): 'CASCADE',
    ('test_plans', 'agent_id'): 'SET NULL',
    ('test_plans', 'approved_by_id'): 'SET NULL',
    ('test_plans', 'created_by_user_id'): 'SET NULL',
    ('test_plans', 'project_id'): 'CASCADE',
    ('test_plans', 'rejected_by_id'): 'SET NULL',
    ('test_plans', 'source_plan_id'): 'SET NULL',
    ('test_plans', 'source_recon_session_id'): 'SET NULL',
    ('users', 'created_by_id'): 'SET NULL',
    ('user_recovery_codes', 'user_id'): 'CASCADE',
    ('user_sessions', 'user_id'): 'CASCADE',
    ('vulnerabilities', 'host_id'): 'CASCADE',
    ('vulnerabilities', 'port_id'): 'SET NULL',
    ('vulnerabilities', 'scan_id'): 'CASCADE',
    ('webhook_configs', 'created_by_id'): 'SET NULL',
    ('webhook_configs', 'project_id'): 'CASCADE',
    ('web_interfaces', 'host_id'): 'CASCADE',
    ('web_interfaces', 'port_id'): 'SET NULL',
    ('web_interfaces', 'project_id'): 'CASCADE',
    ('web_interfaces', 'scan_id'): 'CASCADE',
}


def _normalize(ondelete):
    """SQLAlchemy stores a missing ondelete as None, which DDL-emits as the
    SQL default 'NO ACTION'.  Normalize for comparison against pg_constraint."""
    return (ondelete or "NO ACTION").upper()


def _model_ondelete_map():
    actual = {}
    for table in models.Base.metadata.tables.values():
        for col in table.columns:
            for fk in col.foreign_keys:
                actual[(table.name, col.name)] = _normalize(fk.ondelete)
    return actual


def test_model_fk_ondelete_matches_database():
    """Every model FK must declare the ondelete the live DB enforces."""
    actual = _model_ondelete_map()
    drift = []
    for key, expected in EXPECTED_ONDELETE.items():
        if key not in actual:
            # Table not declared by the models (e.g. intentionally dropped).
            continue
        if actual[key] != expected:
            drift.append(f"{key[0]}.{key[1]}: model={actual[key]} expected={expected}")
    assert not drift, (
        "Model FK ondelete drifted from the DB contract:\n  " + "\n  ".join(sorted(drift))
    )


def test_no_unmapped_model_fks():
    """A model FK absent from EXPECTED_ONDELETE means a new/renamed FK that was
    never reconciled with the DB contract — add it to the map in this commit."""
    actual = _model_ondelete_map()
    unmapped = sorted(f"{t}.{c}" for (t, c) in actual if (t, c) not in EXPECTED_ONDELETE)
    assert not unmapped, (
        "Model FKs not in the ondelete contract (add them to EXPECTED_ONDELETE):\n  "
        + "\n  ".join(unmapped)
    )
