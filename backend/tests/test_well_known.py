"""``/.well-known/networkmapper.json`` states only what the server backs.

Agents and auditors read ``safety_properties`` as fact, so every claim in it
must be true of the running server, and a claim that stopped being true must
disappear rather than linger.  Moved here from ``test_recon_service.py``
(deleted with recon runs in v2.433.0, which took this guard with it while two
of its claims — plan approval, approve-by-exception — had become false).
"""


def _well_known(client, db_session):
    from app.db.models_auth import SystemIdentity
    if db_session.query(SystemIdentity).first() is None:
        db_session.add(SystemIdentity(instance_id="test-wellknown-xyz"))
        db_session.commit()
    resp = client.get("/.well-known/networkmapper.json")
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_identifies_the_instance(client, db_session):
    body = _well_known(client, db_session)
    # The path keeps the pre-rename name (agents have it baked in); the
    # product name is current.
    assert body["name"] == "BlueStick"
    assert body["instance_id"]


def test_claims_only_what_the_server_enforces(client, db_session):
    props = _well_known(client, db_session)["safety_properties"]
    # What the server enforces.
    assert props["server_executes_commands"] is False
    assert props["agent_authority"] == "operator_project_role"
    assert props["agent_key_binding"] == "project_session"
    assert props["agent_keys_time_limited"] is True
    assert props["agent_keys_renewable"] is True
    # Recorded, and kept for the retention window (default 90 days; 0 = kept).
    assert props["audit_trail_recorded"] is True
    assert props["audit_trail_retention_days"] == 90
    # What it cannot: commands run on the operator's machine, so approval is
    # the operator's, held by the agent and the client sandbox.
    assert props["command_approval"] == "operator_driven"
    assert props["command_approval_enforced_by"] == "agent_and_client_sandbox"
    # The set is closed: a new claim is added here, with what backs it, or
    # not published.
    assert set(props) == {
        "server_executes_commands", "agent_authority", "agent_key_binding",
        "agent_keys_time_limited", "agent_keys_renewable",
        "audit_trail_recorded", "audit_trail_retention_days",
        "command_approval", "command_approval_enforced_by",
    }


def test_the_published_retention_is_the_configured_one(client, db_session, monkeypatch):
    """The window the purge loop uses is the window published; 0 means kept."""
    monkeypatch.setenv("AGENT_API_CALL_RETENTION_DAYS", "30")
    assert _well_known(client, db_session)["safety_properties"]["audit_trail_retention_days"] == 30
    monkeypatch.setenv("AGENT_API_CALL_RETENTION_DAYS", "0")
    assert _well_known(client, db_session)["safety_properties"]["audit_trail_retention_days"] == 0


def test_retired_claims_stay_retired(client, db_session):
    props = _well_known(client, db_session)["safety_properties"]
    for retired in (
        # v2.371.0 — untrue by design (approve-by-exception; one session per key).
        "all_commands_require_user_approval",
        "no_autonomous_execution",
        "agent_keys_scope_bound",
        # v2.433.0 — there is no plan approval.
        "plan_execution_requires_human_approval",
        # Rows are purged after AGENT_API_CALL_RETENTION_DAYS: recorded, not
        # persistent (``audit_trail_recorded`` + ``audit_trail_retention_days``).
        "audit_trail_persistent",
    ):
        assert retired not in props, f"{retired} is not true and must not be published"
