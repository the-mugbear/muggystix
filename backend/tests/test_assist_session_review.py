"""The session review surface (v2.284.0).

A session's work has to be readable after the fact: its notes (written under
the operator's own name), counts that let the list say which sessions did
anything, how the agent reached it, the authority it acted with — and project
scoping that keeps one project's session bodies out of another's.

v2.449.0 — these read the ONE route family, keyed by the session id:
``GET /agent-sessions`` (list), ``/agent-sessions/{id}`` (the row),
``/agent-sessions/{id}/notes`` and ``/agent-sessions/{id}/api-activity``.  They
were written against ``/assist/sessions[/{id}]`` and
``/assist-sessions/{id}/api-activity``, keyed by the ``assist_sessions``
pointer row.  Two things those routes did are deliberately not here:

* a DERIVED status (an expired key read as ``ended``) and a filter on it.  The
  one list reports the stored status with ``key_expires_at`` /
  ``renewable_until`` — an expired key inside the renewal window is resumable,
  not over (v2.340.0); see ``test_assist_session_key_expiry.py``.  What is
  kept, below, is that the status filter and the paging agree and that list
  and detail cannot disagree.
* notes inline in the detail read: they are their own route now.
"""
from __future__ import annotations

from app.db.models import Annotation, Host
from app.db.models_agent import AgentApiCall, AgentSession


def _start(client, project_id, **body):
    r = client.post(f"/api/v1/projects/{project_id}/assist/start", json=body or {})
    assert r.status_code == 201, r.text
    return r.json()


def _sid(started) -> int:
    return started["agent_session_id"]


def _detail(client, project_id, session_id, expect=200):
    r = client.get(f"/api/v1/projects/{project_id}/agent-sessions/{session_id}")
    assert r.status_code == expect, r.text
    return r.json() if expect == 200 else None


def _list(client, project_id, **params):
    r = client.get(f"/api/v1/projects/{project_id}/agent-sessions", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _host(db_session, project_id, ip="10.0.0.9"):
    host = Host(project_id=project_id, ip_address=ip, state="up", hostname="ftp01")
    db_session.add(host)
    db_session.commit()
    db_session.refresh(host)
    return host


def _agent_note(db_session, *, session, host, body="vsftpd 2.3.4 on 21"):
    """A note as the agent write path records it — attributed to the operator,
    marked agent-authored, carrying the session id."""
    note = Annotation(
        host_id=host.id,
        project_id=session.project_id,
        user_id=session.started_by_id,
        body=body,
        actor_type="agent",
        agent_session_id=session.id,
    )
    db_session.add(note)
    db_session.commit()
    return note


def test_the_notes_the_session_wrote_are_readable(client, db_session, test_project):
    """Notes are what the agent put the operator's name on. A review page
    without them is a review of nothing."""
    sid = _sid(_start(client, test_project.id))
    session = db_session.get(AgentSession, sid)
    host = _host(db_session, test_project.id)
    _agent_note(db_session, session=session, host=host)

    assert _detail(client, test_project.id, sid)["note_count"] == 1
    body = client.get(f"/api/v1/projects/{test_project.id}/agent-sessions/{sid}/notes").json()
    assert body["total"] == 1
    note = body["items"][0]
    assert note["body"] == "vsftpd 2.3.4 on 21"
    # Resolved to the host, so a reviewer isn't left with a bare id.
    assert note["host_id"] == host.id
    assert note["host_ip"] == "10.0.0.9"
    assert note["hostname"] == "ftp01"


def test_detail_carries_the_agent_attribution(client, db_session, test_project):
    """Which agent and model did the work is part of the audit answer, not just
    live context for the agent. (The environment the detail used to carry went
    with the environment probe.)"""
    sid = _sid(_start(client, test_project.id))
    # Attribution is written on the session row (MCP handshake / agent_model
    # on the agent's writes), which is what the detail reads.
    base = db_session.get(AgentSession, sid)
    base.generated_by_model = "claude-opus-5"
    base.generated_by_tool = "claude-code"
    db_session.commit()

    body = _detail(client, test_project.id, sid)
    assert "environment" not in body
    assert "environment_probed" not in body
    assert body["generated_by_model"] == "claude-opus-5"
    assert body["generated_by_tool"] == "claude-code"
    assert body["prompt_version"]


def test_the_list_says_which_sessions_actually_did_anything(
    client, db_session, test_project
):
    """A session with no calls is the common dead end — key minted, prompt never
    pasted. Without the counts it reads identically to one that did the work,
    and the reviewer opens both to find out."""
    busy_start = _start(client, test_project.id)
    busy = _sid(busy_start)
    idle = _sid(_start(client, test_project.id))

    db_session.add(
        AgentApiCall(
            project_id=test_project.id,
            # agent_id is required by the attribution CHECK — a row without it
            # is only legal for pre-auth failures, which carry an error_class.
            agent_id=busy_start["agent_id"],
            agent_session_id=busy,
            method="GET",
            path="/api/v1/agent/assist/hosts",
            status_code=200,
            duration_ms=5,
        )
    )
    db_session.commit()

    rows = {r["id"]: r for r in _list(client, test_project.id)["sessions"]}
    assert rows[busy]["call_count"] == 1
    assert rows[idle]["call_count"] == 0
    # v2.331.0 — the row also says HOW the agent reached the session, from
    # observed calls. A hand-inserted row with no transport marker is a direct
    # call; no rows at all is "never connected".
    assert rows[busy]["connection"] == "curl"
    assert rows[busy]["first_call_at"] is not None
    assert rows[idle]["connection"] == "none"
    assert rows[idle]["first_call_at"] is None


def test_list_and_detail_name_the_operator_by_full_name(
    client, db_session, test_project, test_user
):
    """The page shows who started a session by display name, falling back to
    the username."""
    sid = _sid(_start(client, test_project.id))
    _start(client, test_project.id)

    rows = _list(client, test_project.id)["sessions"]
    assert len(rows) == 2
    for row in rows:
        assert row["user_username"] == test_user.username
        assert row["user_full_name"] == "Test Admin"
    assert _detail(client, test_project.id, sid)["user_full_name"] == "Test Admin"

    # No display name set: null, so the client falls back to the username.
    test_user.full_name = None
    db_session.commit()
    rows = _list(client, test_project.id)["sessions"]
    assert all(r["user_full_name"] is None for r in rows)
    assert _detail(client, test_project.id, sid)["user_full_name"] is None


def test_connection_state_comes_from_observed_calls_not_the_probe(
    client, db_session, test_project
):
    """"Not yet connected" used to read the environment probe, which measures
    the wrong thing: a client can call tools over MCP without ever posting it,
    and a curl can post it without MCP being involved. The state now comes from
    the audit log, and the transport marker on each row is set server-side by
    the MCP loopback — a direct client cannot claim it."""
    over_mcp = _start(client, test_project.id)
    over_curl = _start(client, test_project.id)
    mcp_sid, curl_sid = _sid(over_mcp), _sid(over_curl)

    # One authenticated tool call through the transport, no probe.
    r = client.post(
        "/api/v1/mcp",
        json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "agent_identity", "arguments": {}},
        },
        headers={"X-API-Key": over_mcp["api_key"]},
    )
    assert r.status_code == 200, r.text
    assert not r.json()["result"].get("isError"), r.text

    # The same call by direct HTTP — the pasted-prompt path — with a header
    # that tries to pass itself off as the MCP transport.
    r = client.get(
        "/api/v1/agent/identity",
        headers={"X-API-Key": over_curl["api_key"], "X-BlueStick-MCP": "1"},
    )
    assert r.status_code == 200, r.text

    rows = {row["id"]: row for row in _list(client, test_project.id)["sessions"]}
    assert rows[mcp_sid]["connection"] == "mcp"
    assert rows[curl_sid]["connection"] == "curl"

    # The audit rows carry the marker the list derived this from.
    mcp_rows = db_session.query(AgentApiCall).filter(
        AgentApiCall.agent_session_id == mcp_sid
    ).all()
    assert mcp_rows and all(row.via_mcp is True for row in mcp_rows)
    curl_rows = db_session.query(AgentApiCall).filter(
        AgentApiCall.agent_session_id == curl_sid
    ).all()
    assert curl_rows and all(row.via_mcp is False for row in curl_rows)

    # The detail view is built from the same row, so it agrees.
    detail = _detail(client, test_project.id, mcp_sid)
    assert detail["connection"] == "mcp"
    assert detail["call_count"] == len(mcp_rows)


def test_activity_feed_is_scoped_to_the_one_session(client, db_session, test_project):
    """Two operators can run agents at once; a feed that mixed them would be
    useless for the question it exists to answer."""
    first_start = _start(client, test_project.id)
    second_start = _start(client, test_project.id)
    first, second = _sid(first_start), _sid(second_start)
    db_session.add_all([
        AgentApiCall(
            project_id=test_project.id, agent_id=first_start["agent_id"],
            agent_session_id=first, method="GET",
            path="/api/v1/agent/assist/hosts", status_code=200, duration_ms=4,
        ),
        AgentApiCall(
            project_id=test_project.id, agent_id=second_start["agent_id"],
            agent_session_id=second, method="GET",
            path="/api/v1/agent/assist/scopes", status_code=200, duration_ms=4,
        ),
    ])
    db_session.commit()

    body = client.get(
        f"/api/v1/projects/{test_project.id}/agent-sessions/{first}/api-activity"
    ).json()
    assert body["total"] == 1
    assert body["items"][0]["path"].endswith("/hosts")


def test_activity_feed_lists_the_calls_the_key_made(client, db_session, test_project):
    """v2.338.2 — the page said "141 API calls" over an empty activity list:
    the count and the feed matched calls on different columns.  There is one
    column now; a real call through the key must show up in both, and a second
    session's in neither."""
    started = _start(client, test_project.id)
    other = _start(client, test_project.id)
    sid = _sid(started)
    for body, n in ((started, 2), (other, 1)):
        for _ in range(n):
            r = client.get("/api/v1/agent/identity", headers={"X-API-Key": body["api_key"]})
            assert r.status_code == 200, r.text

    feed = client.get(
        f"/api/v1/projects/{test_project.id}/agent-sessions/{sid}/api-activity"
    ).json()
    assert feed["total"] == 2, feed
    assert all(i["path"] == "/api/v1/agent/identity" for i in feed["items"])
    detail = _detail(client, test_project.id, sid)
    assert detail["call_count"] == feed["total"], "count and feed must agree"
    assert client.get(
        f"/api/v1/projects/{test_project.id}/agent-sessions/999999/api-activity"
    ).status_code == 404


def test_a_session_from_another_project_is_not_readable(
    client, db_session, test_project
):
    """The reads carry note bodies and the operator's stated purpose, so a
    session id from a project you can see must not resolve against one you
    cannot."""
    from app.db.models_project import Project

    other = Project(name="other-engagement", slug="other-engagement", status="active")
    db_session.add(other)
    db_session.commit()
    db_session.refresh(other)

    sid = _sid(_start(client, other.id, purpose="theirs"))
    # Same session id, wrong project in the path.
    _detail(client, test_project.id, sid, expect=404)
    for tail in ("notes", "api-activity"):
        r = client.get(f"/api/v1/projects/{test_project.id}/agent-sessions/{sid}/{tail}")
        assert r.status_code == 404, (tail, r.text)
    assert sid not in [r["id"] for r in _list(client, test_project.id)["sessions"]]


def test_status_filter_paginates_rather_than_slicing_a_prefix(
    client, db_session, test_project
):
    """The filter and the pagination have to agree. The first shape took the
    newest N rows, filtered in Python, then sliced — so on a project with more
    sessions than that window, an older ``ended`` one was unreachable,
    silently. Filtering in SQL means offset/limit page the filtered set, and
    ``total`` counts it."""
    ids = [_sid(_start(client, test_project.id)) for _ in range(5)]
    # End the two OLDEST sessions — the ones a prefix-slice would miss.
    for sid in ids[:2]:
        assert client.post(
            f"/api/v1/projects/{test_project.id}/agent-sessions/{sid}/end"
        ).status_code == 204

    # Paging the filtered set: one row per page, and the two pages differ.
    first = _list(client, test_project.id, status="ended", limit=1, offset=0)
    second = _list(client, test_project.id, status="ended", limit=1, offset=1)
    assert first["total"] == second["total"] == 2
    assert len(first["sessions"]) == 1 and len(second["sessions"]) == 1
    assert {first["sessions"][0]["id"], second["sessions"][0]["id"]} == set(ids[:2])
    assert all(r["status"] == "ended" for r in first["sessions"] + second["sessions"])
    # Past the end is empty, not a repeat.
    assert _list(client, test_project.id, status="ended", limit=1, offset=2)["sessions"] == []

    # And the live ones are reachable through the same paging on the other side.
    active = _list(client, test_project.id, status="active", limit=50, offset=0)
    assert {r["id"] for r in active["sessions"]} == set(ids[2:]) and active["total"] == 3


def test_pages_are_newest_first_and_do_not_overlap(client, db_session, test_project):
    """One query, ordered and cut in SQL (v2.449.0 — it was a query per table,
    merged in Python).  Pages must partition the list in order."""
    ids = [_sid(_start(client, test_project.id)) for _ in range(5)]
    whole = [r["id"] for r in _list(client, test_project.id)["sessions"]]
    assert sorted(whole) == sorted(ids)
    paged = []
    for offset in (0, 2, 4):
        paged += [r["id"] for r in _list(client, test_project.id, limit=2, offset=offset)["sessions"]]
    assert paged == whole


def test_one_definition_of_status_across_list_and_detail(
    client, db_session, test_project
):
    """List and detail briefly derived this separately. Two surfaces disagreeing
    about whether a session is live is the failure that matters — the panel says
    you hold a live key, the page says the session is over.  The detail read IS
    the list's row for one id, so they cannot differ."""
    from datetime import datetime, timezone

    from app.db.models_auth import APIKey

    sid = _sid(_start(client, test_project.id))
    db_session.query(APIKey).filter(APIKey.agent_session_id == sid).update(
        {"expires_at": datetime.now(timezone.utc)}, synchronize_session=False
    )
    db_session.commit()

    listed = next(r for r in _list(client, test_project.id)["sessions"] if r["id"] == sid)
    detail = _detail(client, test_project.id, sid)
    assert listed == detail


def _listed(client, project_id, sid):
    return next(r for r in _list(client, project_id)["sessions"] if r["id"] == sid)


def test_authority_is_the_operators_project_role(client, db_session, test_project, test_user):
    """v2.402.0 — the Authority column said "as <operator>", repeating Started
    by. The authority a session acts with is its operator's PROJECT ROLE
    (``enforce_agent_operator_access``), so that is what list and detail carry."""
    from app.db.models_auth import UserRole
    from app.db.models_project import ProjectMembership

    sid = _sid(_start(client, test_project.id))

    # The fixture user is a global admin with no membership: the gate lets them
    # through, and nothing else describes that authority honestly.
    assert _listed(client, test_project.id, sid)["operator_role"] == "global_admin"
    assert _detail(client, test_project.id, sid)["operator_role"] == "global_admin"

    # A plain member acts with their membership role, re-read now.
    membership = ProjectMembership(
        project_id=test_project.id, user_id=test_user.id, role="analyst"
    )
    db_session.add(membership)
    test_user.role = UserRole.MEMBER
    db_session.commit()
    assert _listed(client, test_project.id, sid)["operator_role"] == "analyst"
    assert _detail(client, test_project.id, sid)["operator_role"] == "analyst"

    # A global admin whose membership already says admin reads as that role.
    membership.role = "admin"
    test_user.role = UserRole.ADMIN
    db_session.commit()
    assert _listed(client, test_project.id, sid)["operator_role"] == "admin"
