"""The assist session review surface (v2.284.0).

Plans and recon sessions have had a detail view and an API-activity feed since
the audit log existed. Assist had neither: the only place a session appeared was
the start dialog's live-sessions panel, so the one workflow an operator runs
interactively — and the only one that can write notes under their own name — was
the one whose work they could not look back at. The rows were being recorded the
whole time; nothing read them back.

These tests pin what the review page needs to be worth opening: the session's
own output (notes) rather than only its metadata, counts that let the list say
which sessions did anything, and the project scoping that keeps one project's
session bodies out of another's.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.db.models import Annotation, Host, NoteStatus
from app.db.models_agent import AgentApiCall, AssistSession


def _start(client, project_id, **body):
    r = client.post(f"/api/v1/projects/{project_id}/assist/start", json=body or {})
    assert r.status_code == 201, r.text
    return r.json()


def _detail(client, project_id, session_id, expect=200):
    r = client.get(f"/api/v1/projects/{project_id}/assist/sessions/{session_id}")
    assert r.status_code == expect, r.text
    return r.json() if expect == 200 else None


def _host(db_session, project_id, ip="10.0.0.9"):
    host = Host(project_id=project_id, ip_address=ip, state="up", hostname="ftp01")
    db_session.add(host)
    db_session.commit()
    db_session.refresh(host)
    return host


def _agent_note(db_session, *, session, host, body="vsftpd 2.3.4 on 21"):
    """A note as the agent write path records it — attributed to the operator,
    marked agent-authored, carrying the unified agent-session id."""
    note = Annotation(
        host_id=host.id,
        project_id=session.project_id,
        user_id=session.started_by_id,
        body=body,
        status=NoteStatus.OPEN,
        actor_type="agent",
        agent_session_id=session.agent_session_id,
    )
    db_session.add(note)
    db_session.commit()
    return note


def test_detail_returns_the_notes_the_session_wrote(client, db_session, test_project):
    """Notes are the session's only durable output — everything else it did was
    a read. A review page without them is a review of nothing."""
    started = _start(client, test_project.id)
    sid = started["assist_session_id"]
    session = db_session.get(AssistSession, sid)
    host = _host(db_session, test_project.id)
    _agent_note(db_session, session=session, host=host)

    body = _detail(client, test_project.id, sid)
    assert body["note_count"] == 1
    note = body["notes"][0]
    assert note["body"] == "vsftpd 2.3.4 on 21"
    # Resolved to the host, so a reviewer isn't left with a bare id.
    assert note["host_id"] == host.id
    assert note["host_ip"] == "10.0.0.9"
    assert note["hostname"] == "ftp01"


def test_detail_carries_the_environment_the_agent_reported(
    client, db_session, test_project
):
    """Which machine the run happened on is part of the audit answer, not just
    live context for the agent."""
    started = _start(client, test_project.id)
    sid = started["assist_session_id"]
    session = db_session.get(AssistSession, sid)
    session.environment = {"os_family": "linux", "shell": "bash"}
    session.environment_probed_at = datetime.now(timezone.utc)
    session.generated_by_model = "claude-opus-5"
    session.generated_by_tool = "claude-code"
    db_session.commit()

    body = _detail(client, test_project.id, sid)
    assert body["environment"]["os_family"] == "linux"
    assert body["environment_probed"] is True
    assert body["agent_model"] == "claude-opus-5"
    assert body["agent_tool"] == "claude-code"


def test_the_list_says_which_sessions_actually_did_anything(
    client, db_session, test_project
):
    """A session with no calls is the common dead end — key minted, prompt never
    pasted. Without the counts it reads identically to one that did the work,
    and the reviewer opens both to find out."""
    busy_start = _start(client, test_project.id)
    busy = busy_start["assist_session_id"]
    idle = _start(client, test_project.id)["assist_session_id"]

    db_session.add(
        AgentApiCall(
            project_id=test_project.id,
            # agent_id is required by the attribution CHECK — a row without it
            # is only legal for pre-auth failures, which carry an error_class.
            agent_id=busy_start["agent_id"],
            assist_session_id=busy,
            method="GET",
            path="/api/v1/agent/assist/hosts",
            status_code=200,
            duration_ms=5,
        )
    )
    db_session.commit()

    rows = {
        r["id"]: r
        for r in client.get(f"/api/v1/projects/{test_project.id}/assist/sessions").json()
    }
    assert rows[busy]["call_count"] == 1
    assert rows[idle]["call_count"] == 0


def test_activity_feed_is_scoped_to_the_one_session(client, db_session, test_project):
    """Two operators can run assist at once; a feed that mixed them would be
    useless for the question it exists to answer."""
    first_start = _start(client, test_project.id)
    second_start = _start(client, test_project.id)
    first, second = first_start["assist_session_id"], second_start["assist_session_id"]
    db_session.add_all([
        AgentApiCall(
            project_id=test_project.id, agent_id=first_start["agent_id"],
            assist_session_id=first, method="GET",
            path="/api/v1/agent/assist/hosts", status_code=200, duration_ms=4,
        ),
        AgentApiCall(
            project_id=test_project.id, agent_id=second_start["agent_id"],
            assist_session_id=second, method="GET",
            path="/api/v1/agent/assist/scopes", status_code=200, duration_ms=4,
        ),
    ])
    db_session.commit()

    body = client.get(
        f"/api/v1/projects/{test_project.id}/assist-sessions/{first}/api-activity"
    ).json()
    assert body["total"] == 1
    assert body["items"][0]["path"].endswith("/hosts")


def test_a_session_from_another_project_is_not_readable(
    client, db_session, test_project
):
    """The detail response carries note bodies and the operator's stated
    purpose, so a session id from a project you can see must not resolve
    against one you cannot."""
    from app.db.models_project import Project

    other = Project(name="other-engagement", slug="other-engagement", status="active")
    db_session.add(other)
    db_session.commit()
    db_session.refresh(other)

    sid = _start(client, other.id)["assist_session_id"]
    # Same session id, wrong project in the path.
    _detail(client, test_project.id, sid, expect=404)


def test_the_list_filters_by_effective_status(client, db_session, test_project):
    """The filter has to agree with what the caller is shown — filtering on the
    stored value would list sessions as active that the same response reports
    as ended."""
    from app.db.models_auth import APIKey

    live = _start(client, test_project.id, ttl_hours=6)["assist_session_id"]
    dead = _start(client, test_project.id)["assist_session_id"]
    dead_agent_session = (
        db_session.query(AssistSession.agent_session_id)
        .filter(AssistSession.id == dead).scalar()
    )
    db_session.query(APIKey).filter(APIKey.agent_session_id == dead_agent_session).update(
        {"expires_at": datetime.now(timezone.utc)}, synchronize_session=False
    )
    db_session.commit()

    active = client.get(
        f"/api/v1/projects/{test_project.id}/assist/sessions?status=active"
    ).json()
    ended = client.get(
        f"/api/v1/projects/{test_project.id}/assist/sessions?status=ended"
    ).json()

    assert live in [r["id"] for r in active]
    assert dead not in [r["id"] for r in active]
    assert dead in [r["id"] for r in ended]


def test_status_filter_paginates_rather_than_slicing_a_prefix(
    client, db_session, test_project
):
    """The filter and the pagination have to agree. The first shape took the
    newest N rows, derived the status in Python, then sliced — so on a project
    with more sessions than that window, an older `ended` one was unreachable,
    silently. Deriving in SQL means offset/limit page the filtered set."""
    from app.db.models_auth import APIKey

    ids = [_start(client, test_project.id)["assist_session_id"] for _ in range(5)]
    # Kill the two OLDEST sessions' keys — the ones a prefix-slice would miss.
    dead_agent_sessions = [
        r[0] for r in db_session.query(AssistSession.agent_session_id)
        .filter(AssistSession.id.in_(ids[:2]))
    ]
    db_session.query(APIKey).filter(APIKey.agent_session_id.in_(dead_agent_sessions)).update(
        {"expires_at": datetime.now(timezone.utc)}, synchronize_session=False
    )
    db_session.commit()

    def page(**params):
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        return client.get(
            f"/api/v1/projects/{test_project.id}/assist/sessions?{qs}"
        ).json()

    # Paging the filtered set: one row per page, and the two pages differ.
    first = page(status="ended", limit=1, offset=0)
    second = page(status="ended", limit=1, offset=1)
    assert len(first) == 1 and len(second) == 1
    assert first[0]["id"] != second[0]["id"]
    assert {first[0]["id"], second[0]["id"]} == set(ids[:2])
    assert all(r["status"] == "ended" for r in first + second)

    # And the live ones are reachable through the same paging on the other side.
    active = page(status="active", limit=50, offset=0)
    assert {r["id"] for r in active} == set(ids[2:])


def test_one_definition_of_active_across_list_and_detail(
    client, db_session, test_project
):
    """List and detail briefly derived this separately. Two surfaces disagreeing
    about whether a session is live is the failure that matters — the panel says
    you hold a live key, the page says the session is over."""
    from app.db.models_auth import APIKey

    sid = _start(client, test_project.id)["assist_session_id"]
    sid_agent_session = (
        db_session.query(AssistSession.agent_session_id)
        .filter(AssistSession.id == sid).scalar()
    )
    db_session.query(APIKey).filter(APIKey.agent_session_id == sid_agent_session).update(
        {"expires_at": datetime.now(timezone.utc)}, synchronize_session=False
    )
    db_session.commit()

    listed = next(
        r
        for r in client.get(
            f"/api/v1/projects/{test_project.id}/assist/sessions"
        ).json()
        if r["id"] == sid
    )
    detail = _detail(client, test_project.id, sid)
    assert listed["status"] == detail["status"] == "ended"
