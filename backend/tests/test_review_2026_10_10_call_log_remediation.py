"""Review 2026-10-10, H12 — a project viewer read remediation write bodies
(contact names, addresses, dates) through the agent call log: the log is
every member's, remediation is the auditor's and up.

The content of a remediation call is not kept in the log at all — the row
still says which call was made, with what result — and a row written before
that rule is answered the same way.
"""
import pytest

from app.api.deps import get_current_user
from app.db import models
from app.db.models_agent import AgentApiCall
from app.db.models_auth import User, UserRole
from app.db.models_findings import Finding, FindingHost
from app.db.models_project import ProjectMembership, ProjectRole
from app.db.models_remediation import RemediationPolicy
from app.main import app
from app.services.agent_api_log_service import NOT_RECORDED, keeps_no_content, withhold_private_content

AGENT = "/api/v1/agent/remediation"
EMAIL = "roger.owner@client.example"


@pytest.fixture(autouse=True)
def tracking_on(db_session):
    db_session.add(RemediationPolicy(id=1, enabled=True, days_critical=30, days_high=30,
                                     days_medium=90, days_low=120, due_soon_days=7))
    db_session.commit()


@pytest.fixture()
def world(client, db_session, test_project):
    host = models.Host(project_id=test_project.id, ip_address="10.95.0.1", state="up")
    db_session.add(host)
    db_session.flush()
    finding = Finding(project_id=test_project.id, title="SMB signing not required",
                      severity="high", status="confirmed", source="manual")
    db_session.add(finding)
    db_session.flush()
    db_session.add(FindingHost(finding_id=finding.id, host_id=host.id, host_status="open"))
    viewer = User(id=95001, username="viewer-95001", email="viewer-95001@example.com",
                  hashed_password="$2b$12$abcdefghijklmnopqrstuv", role=UserRole.MEMBER, is_active=True)
    db_session.add(viewer)
    db_session.flush()
    db_session.add(ProjectMembership(project_id=test_project.id, user_id=viewer.id,
                                     role=ProjectRole.VIEWER.value))
    db_session.commit()
    started = client.post(f"/api/v1/projects/{test_project.id}/assist/start", json={})
    assert started.status_code == 201, started.text
    return {
        "host": host.id, "finding": finding.id, "viewer": viewer,
        "session": started.json()["agent_session_id"],
        "key": {"X-API-Key": started.json()["api_key"]},
    }


def _calls_as(client, project, world, user):
    """The session's call log as ``user`` reads it."""
    previous = app.dependency_overrides[get_current_user]
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        resp = client.get(
            f"/api/v1/projects/{project.id}/agent-sessions/{world['session']}/api-activity",
            params={"limit": 200},
        )
    finally:
        app.dependency_overrides[get_current_user] = previous
    assert resp.status_code == 200, resp.text
    return resp


def test_a_viewer_reads_no_remediation_content_in_the_call_log(client, db_session, test_project, test_user, world):
    rows = [{"finding_id": world["finding"], "host_id": world["host"], "contact_email": EMAIL,
             "contact_name": "Roger Owner", "status": "closed",
             "notes": [{"body": "Roger says patched on the 4th", "request_key": "r1"}]}]
    done = client.post(f"{AGENT}/apply", json={"rows": rows}, headers=world["key"])
    assert done.status_code == 200, done.text
    listed = client.get(AGENT, params={"contact": "roger.owner", "host_id": world["host"]}, headers=world["key"])
    assert listed.status_code == 200, listed.text
    # An ordinary write beside them, whose body the log does keep.
    note = client.post(
        f"/api/v1/agent/hosts/{world['host']}/notes", headers=world["key"],
        json={"body": "ordinary note body"},
    )
    assert note.status_code in (200, 201), note.text

    for reader in (world["viewer"], test_user):       # nobody reads it from the log
        resp = _calls_as(client, test_project, world, reader)
        assert "roger" not in resp.text.lower(), reader.username
        assert "patched on the 4th" not in resp.text
        by_path = {}
        for item in resp.json()["items"]:
            by_path.setdefault((item["method"], item["path"]), item)
        applied = by_path[("POST", f"{AGENT}/apply")]
        # The call is still on the record: which, with what result, how big.
        assert applied["status_code"] == 200 and applied["path_template"].endswith("/remediation/apply")
        assert applied["request_body_summary"]["_not_recorded"] is True
        assert applied["request_body_summary"]["_size"] > 0
        read = by_path[("GET", AGENT)]
        assert read["query_params"] == {"contact": NOT_RECORDED, "host_id": NOT_RECORDED}
        assert read["referenced_host_ids"] == [world["host"]]
        ordinary = [i for i in resp.json()["items"] if i["path"].endswith("/notes")]
        assert ordinary and ordinary[0]["request_body_summary"]["body"] == "ordinary note body"

    # ... and it is not in the table either.
    stored = db_session.query(AgentApiCall).filter(AgentApiCall.path.like(f"{AGENT}%")).all()
    assert stored and all("roger" not in str((r.request_body_summary, r.query_params)).lower() for r in stored)


def test_a_row_written_before_the_rule_is_answered_the_same_way(client, db_session, test_project, world):
    from app.db.models_agent import AgentSession
    session = db_session.get(AgentSession, world["session"])
    db_session.add(AgentApiCall(
        agent_id=session.agent_id, project_id=test_project.id, agent_session_id=session.id,
        method="POST", path=f"{AGENT}/apply", path_template=f"{AGENT}/apply",
        request_body_summary={"rows": [{"contact_email": EMAIL}]}, query_params={"contact": "roger"},
        status_code=200, duration_ms=5,
    ))
    db_session.commit()

    resp = _calls_as(client, test_project, world, world["viewer"])
    assert EMAIL not in resp.text and "roger" not in resp.text
    (row,) = [i for i in resp.json()["items"] if i["path"] == f"{AGENT}/apply"]
    assert row["request_body_summary"]["_not_recorded"] is True
    assert row["query_params"] == {"contact": NOT_RECORDED}


def test_which_paths_keep_no_content():
    assert keeps_no_content("/api/v1/agent/remediation")
    assert keeps_no_content("/api/v1/agent/remediation/apply")
    assert keeps_no_content("/api/v1/agent/remediation/hosts/3/events")
    assert not keeps_no_content("/api/v1/agent/remediations")
    assert not keeps_no_content("/api/v1/agent/hosts")
    assert not keeps_no_content(None)
    body, query = {"a": 1}, {"q": "x"}
    assert withhold_private_content("/api/v1/agent/hosts", body, query) == (body, query)
    assert withhold_private_content("/api/v1/agent/remediation", None, None) == (None, None)
