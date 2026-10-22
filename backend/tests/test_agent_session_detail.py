"""v2.432.0 — the agent session is a page of its own.

The session row says what the session did; the detail read
``GET /agent-sessions/{id}`` returns that row; and the caller's End / Resume
rights come with it instead of being guessed from the global role.

v2.442.0 — a session no longer opens "phases" (plans, execution runs): the row
carries ``host_test_count`` and ``evidence_count``, the host tests it proposed
and the evidence records it wrote, counted for THAT session only.

v2.449.0 — a session has ONE id.  Until then a second row
(``assist_sessions``) had its own id sequence — session #72's notes lived under
assist #52 — and the page read notes and API calls by that id.  Notes, calls
and activity counts are now read by the session id.

2.489.0 — and nothing remembers the second id: ``legacy_assist_session_id``
and the deprecated routes that resolved it are gone.  A session has no KIND
either: a row stored before the consolidation reads exactly like any other.
"""
from datetime import datetime, timedelta, timezone

from app.db import models
from app.db.models_agent import AgentApiCall, AgentSession
from app.db.models_host_tests import HostTest
from app.db.models_proposals import EvidenceRecord
from app.db.models_auth import User, UserRole
from app.db.models_project import ProjectMembership, ProjectRole
from app.main import app
from app.api.v1.endpoints.auth import get_current_user


def _start(client, project, purpose="map the DMZ"):
    r = client.post(f"/api/v1/projects/{project.id}/assist/start", json={"purpose": purpose})
    assert r.status_code == 201, r.text
    return r.json()["agent_session_id"]


def _session_id(db, started_id):
    """The session id IS the id the start returned (v2.449.0)."""
    return started_id


def _do_work(db, project, user, session_id, *, tests=2, evidence=3, ip="10.71.0.1"):
    host = models.Host(project_id=project.id, ip_address=ip, state="up")
    db.add(host)
    db.flush()
    for i in range(tests):
        db.add(HostTest(
            project_id=project.id, host_id=host.id, tool="nxc", description=f"t{i}", rationale="r",
            priority="medium", status="proposed", source="agent", agent_session_id=session_id,
            created_by_user_id=user.id, request_key=f"detail-{session_id}-{i}", request_hash="0" * 64,
        ))
    for i in range(evidence):
        db.add(EvidenceRecord(
            project_id=project.id, host_id=host.id, tool="nxc", outcome="info",
            summary=f"e{i}", agent_session_id=session_id,
        ))
    db.commit()


def test_the_detail_read_returns_the_sessions_work_and_detail_row(
    client, db_session, test_project, test_user
):
    assist_id = _start(client, test_project)
    sid = _session_id(db_session, assist_id)
    other_sid = _session_id(db_session, _start(client, test_project, purpose="another"))
    _do_work(db_session, test_project, test_user, sid, tests=2, evidence=3)
    # Another session's work, and a person's, are not this session's.
    _do_work(db_session, test_project, test_user, other_sid, tests=5, evidence=1, ip="10.71.0.2")
    _do_work(db_session, test_project, test_user, None, tests=1, evidence=1, ip="10.71.0.3")

    r = client.get(f"/api/v1/projects/{test_project.id}/agent-sessions/{sid}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == sid
    assert body["purpose"] == "map the DMZ"
    assert (body["host_test_count"], body["evidence_count"]) == (2, 3)
    # ``assist_session_id`` / ``agent_session_id`` named a second id (v2.449.0);
    # ``kind`` / ``session_live`` told kinds of session apart (2.489.0).
    for retired in (
        "phases", "test_plan_id", "target_label", "assist_session_id", "agent_session_id",
        "kind", "session_live", "workflow",
    ):
        assert retired not in body


def test_the_list_row_carries_the_same_counts(client, db_session, test_project, test_user):
    assist_id = _start(client, test_project)
    sid = _session_id(db_session, assist_id)
    idle = _session_id(db_session, _start(client, test_project, purpose="idle"))
    _do_work(db_session, test_project, test_user, sid, tests=4, evidence=1)

    rows = client.get(f"/api/v1/projects/{test_project.id}/agent-sessions").json()["sessions"]
    by_id = {r["id"]: r for r in rows}
    detail = client.get(f"/api/v1/projects/{test_project.id}/agent-sessions/{sid}").json()
    assert (by_id[sid]["host_test_count"], by_id[sid]["evidence_count"]) == (4, 1)
    assert (detail["host_test_count"], detail["evidence_count"]) == (4, 1)
    assert (by_id[idle]["host_test_count"], by_id[idle]["evidence_count"]) == (0, 0)
    # The list has no filter by kind: the route declares no such parameter.
    declared = {
        p["name"] for p in
        app.openapi()["paths"]["/api/v1/projects/{project_id}/agent-sessions"]["get"]["parameters"]
    }
    assert "kind" not in declared and {"model", "tool", "status", "user_id"} <= declared


def test_the_owner_may_end_and_resume_their_active_session(client, db_session, test_project):
    sid = _session_id(db_session, _start(client, test_project))
    body = client.get(f"/api/v1/projects/{test_project.id}/agent-sessions/{sid}").json()
    assert body["can_end"] is True
    assert body["can_resume"] is True
    assert body["operator_role"] == "global_admin"


def test_a_project_admin_may_end_but_not_resume_anothers_session(
    client, db_session, test_project, test_user
):
    sid = _session_id(db_session, _start(client, test_project))
    other = User(
        id=2,  # the fixture user pins id 1 without advancing the sequence
        username="project-admin", email="pa@example.com", full_name="Project Admin",
        hashed_password="x", role=UserRole.MEMBER,
    )
    db_session.add(other)
    db_session.flush()
    db_session.add(ProjectMembership(project_id=test_project.id, user_id=other.id, role=ProjectRole.ADMIN.value))
    db_session.commit()
    app.dependency_overrides[get_current_user] = lambda: other
    try:
        body = client.get(f"/api/v1/projects/{test_project.id}/agent-sessions/{sid}").json()
    finally:
        app.dependency_overrides[get_current_user] = lambda: test_user
    assert body["can_end"] is True
    assert body["can_resume"] is False


def test_an_ended_session_offers_neither(client, db_session, test_project):
    sid = _session_id(db_session, _start(client, test_project))
    assert client.post(f"/api/v1/projects/{test_project.id}/agent-sessions/{sid}/end").status_code == 204
    body = client.get(f"/api/v1/projects/{test_project.id}/agent-sessions/{sid}").json()
    assert body["status"] == "ended"
    assert body["can_end"] is False and body["can_resume"] is False


def test_a_legacy_row_or_another_projects_session_is_not_found(
    client, db_session, test_project, test_agent, test_user
):
    legacy = AgentSession(
        workflow="execution", project_id=test_project.id, agent_id=test_agent.id,
        started_by_id=test_user.id, status="active",
    )
    db_session.add(legacy)
    db_session.commit()
    base = f"/api/v1/projects/{test_project.id}/agent-sessions"
    assert client.get(f"{base}/{legacy.id}").status_code == 404
    assert client.get(f"{base}/{legacy.id}/notes").status_code == 404
    assert client.get(f"{base}/999999").status_code == 404
    assert client.get(f"{base}/999999/notes").status_code == 404
    assert client.get(f"{base}/999999/api-activity").status_code == 404


def _other_project(db):
    from app.db.models_project import Project

    other = Project(name="Other engagement", slug="other-engagement", status="active")
    db.add(other)
    db.commit()
    return other


def test_another_projects_session_is_not_read_through_this_one(
    client, db_session, test_project, test_agent, test_user
):
    """The path's project scopes every by-id read: a session id from another
    project must not give up its purpose, notes or calls."""
    other = _other_project(db_session)
    theirs = AgentSession(
        workflow="project", project_id=other.id, started_by_id=test_user.id,
        status="active", purpose="their purpose",
    )
    db_session.add(theirs)
    db_session.commit()
    base = f"/api/v1/projects/{test_project.id}"
    for path in (
        f"/agent-sessions/{theirs.id}",
        f"/agent-sessions/{theirs.id}/notes",
        f"/agent-sessions/{theirs.id}/api-activity",
    ):
        r = client.get(base + path)
        assert r.status_code == 404, (path, r.text)
        assert "their purpose" not in r.text
    assert client.post(f"{base}/agent-sessions/{theirs.id}/end").status_code == 404
    db_session.expire_all()
    assert db_session.get(AgentSession, theirs.id).status == "active"


def test_a_session_from_before_the_consolidation_reads_like_any_other(
    client, db_session, test_project, test_agent, test_user
):
    """A session stored before v2.337.0 (``workflow`` ``assist``) has the same
    page and the same fields as one started today: no answer says which it is
    (2.489.0), and what it did is counted the same way — its tests and
    evidence included, which were computed for ``project`` rows only."""
    legacy = AgentSession(
        workflow="assist", project_id=test_project.id, agent_id=test_agent.id,
        started_by_id=test_user.id, status="ended", purpose="find FTP",
        generated_by_model="model-x",
    )
    db_session.add(legacy)
    db_session.flush()
    db_session.add(AgentApiCall(
        agent_id=test_agent.id, project_id=test_project.id, agent_session_id=legacy.id,
        method="GET", path="/api/v1/agent/assist/hosts", status_code=200, duration_ms=3,
    ))
    db_session.commit()
    _do_work(db_session, test_project, test_user, legacy.id, tests=2, evidence=1, ip="10.71.3.1")
    today = _start(client, test_project, purpose="started today")
    base = f"/api/v1/projects/{test_project.id}"

    body = client.get(f"{base}/agent-sessions/{legacy.id}").json()
    assert body["id"] == legacy.id
    assert set(body) == set(client.get(f"{base}/agent-sessions/{today}").json())
    assert "kind" not in body and "session_live" not in body
    assert body["purpose"] == "find FTP" and body["generated_by_model"] == "model-x"
    assert body["call_count"] == 1 and body["connection"] == "curl"
    assert (body["host_test_count"], body["evidence_count"]) == (2, 1)
    # It is over: nothing to end or resume.
    assert body["can_end"] is False and body["can_resume"] is False
    assert client.get(f"{base}/agent-sessions/{legacy.id}/api-activity").json()["total"] == 1
    assert client.get(f"{base}/agent-sessions/{legacy.id}/notes").json() == {"total": 0, "items": []}


def test_the_offered_buttons_mirror_the_routes_for_an_old_active_session(
    client, db_session, test_project, test_agent, test_user
):
    """``can_end`` / ``can_resume`` say what the routes will do.  End takes any
    active session; Resume refuses a row not stored as ``project``.  Until
    2.489.0 a still-active session from before the consolidation was offered
    NEITHER, although its owner could end it (the route said 204)."""
    old = AgentSession(
        workflow="assist", project_id=test_project.id, agent_id=test_agent.id,
        started_by_id=test_user.id, status="active", purpose="left open",
    )
    db_session.add(old)
    db_session.commit()
    base = f"/api/v1/projects/{test_project.id}/agent-sessions"

    body = client.get(f"{base}/{old.id}").json()
    assert body["can_end"] is True
    assert body["can_resume"] is False
    refused = client.post(f"{base}/{old.id}/resume", json={})
    assert refused.status_code == 409
    assert "assist" not in refused.json()["detail"]   # no kind is named to the reader
    assert client.post(f"{base}/{old.id}/end").status_code == 204
    assert client.get(f"{base}/{old.id}").json()["can_end"] is False


# --- activity and notes, by the session id -----------------------------------

def _call(db, project, agent, session_id, *, via_mcp, minutes_ago, path="/api/v1/agent/assist/hosts"):
    db.add(AgentApiCall(
        agent_id=agent.id, project_id=project.id, agent_session_id=session_id,
        method="GET", path=path, status_code=200, duration_ms=5, via_mcp=via_mcp,
        created_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
    ))


def test_the_row_says_how_much_the_session_did(
    client, db_session, test_project, test_agent, test_user
):
    busy = _start(client, test_project)
    curl_only = _start(client, test_project, purpose="curl")
    idle = _start(client, test_project, purpose="idle")
    _call(db_session, test_project, test_agent, busy, via_mcp=False, minutes_ago=30)
    _call(db_session, test_project, test_agent, busy, via_mcp=True, minutes_ago=20)
    _call(db_session, test_project, test_agent, busy, via_mcp=None, minutes_ago=10)
    _call(db_session, test_project, test_agent, curl_only, via_mcp=False, minutes_ago=5)
    host = models.Host(project_id=test_project.id, ip_address="10.71.9.1", state="up")
    db_session.add(host)
    db_session.flush()
    for i in range(2):
        db_session.add(models.Annotation(
            host_id=host.id, user_id=test_user.id,
            body=f"note {i}", agent_session_id=busy,
        ))
    db_session.commit()

    base = f"/api/v1/projects/{test_project.id}/agent-sessions"
    rows = {r["id"]: r for r in client.get(base).json()["sessions"]}
    assert (rows[busy]["call_count"], rows[busy]["connection"], rows[busy]["note_count"]) == (3, "mcp", 2)
    assert rows[busy]["first_call_at"] is not None
    assert (rows[curl_only]["call_count"], rows[curl_only]["connection"]) == (1, "curl")
    assert (rows[idle]["call_count"], rows[idle]["connection"], rows[idle]["note_count"]) == (0, "none", 0)
    assert rows[idle]["first_call_at"] is None
    # The detail read is the same row.
    detail = client.get(f"{base}/{busy}").json()
    assert (detail["call_count"], detail["connection"], detail["note_count"]) == (3, "mcp", 2)


def test_the_sessions_notes_are_read_by_the_session_id(
    client, db_session, test_project, test_user
):
    sid = _start(client, test_project)
    other = _start(client, test_project, purpose="another")
    host = models.Host(project_id=test_project.id, ip_address="10.71.9.2", hostname="files01", state="up")
    db_session.add(host)
    db_session.flush()
    for i in range(3):
        db_session.add(models.Annotation(
            host_id=host.id, user_id=test_user.id,
            body=f"mine {i}", agent_session_id=sid,
            created_at=datetime.now(timezone.utc) - timedelta(minutes=10 - i),
        ))
    db_session.add(models.Annotation(
        host_id=host.id, user_id=test_user.id,
        body="another session's", agent_session_id=other,
    ))
    db_session.add(models.Annotation(
        host_id=host.id, user_id=test_user.id, body="a person's",
    ))
    db_session.commit()

    url = f"/api/v1/projects/{test_project.id}/agent-sessions/{sid}/notes"
    body = client.get(url).json()
    assert body["total"] == 3
    assert [n["body"] for n in body["items"]] == ["mine 2", "mine 1", "mine 0"]  # newest first
    assert body["items"][0]["host_ip"] == "10.71.9.2" and body["items"][0]["hostname"] == "files01"
    # ``total`` is every note; ``limit`` cuts the page only.
    cut = client.get(url, params={"limit": 2}).json()
    assert cut["total"] == 3 and len(cut["items"]) == 2


def test_the_api_call_feed_is_read_by_the_session_id(
    client, db_session, test_project, test_agent
):
    sid = _start(client, test_project)
    other = _start(client, test_project, purpose="another")
    _call(db_session, test_project, test_agent, sid, via_mcp=True, minutes_ago=3, path="/api/v1/agent/a")
    _call(db_session, test_project, test_agent, sid, via_mcp=True, minutes_ago=1, path="/api/v1/agent/b")
    _call(db_session, test_project, test_agent, other, via_mcp=True, minutes_ago=2, path="/api/v1/agent/c")
    db_session.commit()

    feed = client.get(f"/api/v1/projects/{test_project.id}/agent-sessions/{sid}/api-activity").json()
    assert feed["total"] == 2
    assert [i["path"] for i in feed["items"]] == ["/api/v1/agent/b", "/api/v1/agent/a"]


# --- one id, and nothing answers for an old one --------------------------------

def test_start_writes_one_row_and_returns_its_id(client, db_session, test_project):
    before = db_session.query(AgentSession).count()
    r = client.post(f"/api/v1/projects/{test_project.id}/assist/start", json={"purpose": "ids"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert db_session.query(AgentSession).count() == before + 1
    row = db_session.get(AgentSession, body["agent_session_id"])
    assert row.workflow == "project" and row.purpose == "ids"
    # There is no second id: not on the row, not in the answer.
    assert not hasattr(row, "legacy_assist_session_id")
    assert "assist_session_id" not in body
    # The prompt and the MCP setup name that id too.
    assert f"#{row.id}" in body["instructions"] or str(row.id) in body["instructions"]


def test_the_old_paths_are_gone_and_nothing_left_is_deprecated(client, test_project):
    """The four by-old-id routes ended with the column that resolved the ids
    (2.489.0; they were ``deprecated`` from v2.449.0).  This replaces
    ``test_an_old_assist_session_id_finds_its_session`` and the "marked
    deprecated" half of this test, which guarded those routes."""
    paths = app.openapi()["paths"]
    root = "/api/v1/projects/{project_id}"
    assert not [p for p in paths if "/assist-sessions" in p or "/assist/sessions" in p]
    sid = _start(client, test_project)
    base = f"/api/v1/projects/{test_project.id}"
    for method, path in (
        ("get", f"/assist-sessions/{sid}"),
        ("get", f"/assist-sessions/{sid}/api-activity"),
        ("get", f"/assist/sessions/{sid}"),
        ("post", f"/assist/sessions/{sid}/end"),
    ):
        assert getattr(client, method)(base + path).status_code == 404, path
    for path in (
        "/agent-sessions/{session_id}",
        "/agent-sessions/{session_id}/notes",
        "/agent-sessions/{session_id}/api-activity",
        "/agent-sessions/{session_id}/end",
        "/agent-sessions/{session_id}/resume",
        "/assist/start",
    ):
        assert not any(op.get("deprecated") for op in paths[root + path].values()), path
    # The list keyed by the second id has no translation to offer.
    assert root + "/assist/sessions" not in paths
    assert client.get(f"/api/v1/projects/{test_project.id}/assist/sessions").status_code in (404, 405)


def test_the_static_rollup_route_is_not_read_as_an_id(client, test_project):
    r = client.get(f"/api/v1/projects/{test_project.id}/agent-sessions/by-model-tool")
    assert r.status_code == 200, r.text
    assert "summary" in r.json()


