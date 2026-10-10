"""A host's review is finished with ONE call — no conclusion (owner, 2026-10-10).

"Not reviewed" → "In review" keeps teammates from duplicating work; "Reviewed"
says the review is complete.  It used to require a conclusion (no_issue /
finding_created / needs_evidence / out_of_scope / duplicate).  It asks for
nothing now, on the people's door and the agents':

* setting Reviewed takes the status alone, and writes no conclusion even when
  a caller still sends one;
* a conclusion stored earlier is kept while the review stands (marking it
  Reviewed again, from the page, the bulk bar or an agent, leaves it and the
  note alone) and goes when the review is re-opened;
* the optional note has its own call (``PATCH /hosts/{id}/follow``), which
  moves nothing else;
* the bulk bar's Reviewed is the same write as the single one.

What a finished review is "not done" for (only a change after it), the
retired ``conclusion:`` query word and Posture's count are pinned where those
live: ``test_operations_redesign.py``, ``test_operations_tabs.py``,
``test_posture.py``.
"""
from datetime import datetime, timedelta, timezone

from app.db import models
from app.db.models import FollowStatus, HostFollow


def _host(db, project_id, ip):
    h = models.Host(ip_address=ip, state="up", project_id=project_id)
    db.add(h)
    db.flush()
    return h


def _base(project):
    return f"/api/v1/projects/{project.id}/hosts"


def _row(db, host, user):
    db.expire_all()
    return db.query(HostFollow).filter_by(host_id=host.id, user_id=user.id).one()


def _agent_headers(client, project):
    started = client.post(f"/api/v1/projects/{project.id}/assist/start", json={})
    assert started.status_code == 201, started.text
    return {"X-API-Key": started.json()["api_key"]}


# ---------------------------------------------------------------------------
# Setting Reviewed needs no conclusion — both doors
# ---------------------------------------------------------------------------

def test_the_peoples_reviewed_is_the_status_and_nothing_else(client, db_session, test_project, test_user):
    from app.main import app

    host = _host(db_session, test_project.id, "10.70.0.1")
    db_session.commit()

    before = datetime.now(timezone.utc)
    r = client.post(f"{_base(test_project)}/{host.id}/follow", json={"status": "reviewed"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "reviewed"
    assert body["review_conclusion"] is None and body["review_summary"] is None

    row = _row(db_session, host, test_user)
    assert row.status == FollowStatus.REVIEWED
    assert row.review_conclusion is None and row.review_summary is None
    assert row.reviewed_at is not None and row.reviewed_at >= before - timedelta(seconds=5)

    # The request has one field.  Nothing about a conclusion is offered, and
    # there is no list of conclusions left to offer.
    schemas = app.openapi()["components"]["schemas"]
    assert set(schemas["HostFollowUpdate"]["properties"]) == {"status"}
    assert schemas["HostFollowUpdate"]["required"] == ["status"]
    assert set(schemas["BulkFollowRequest"]["properties"]) == {"host_ids", "status"}
    import app.schemas.schemas as people_schemas
    assert not hasattr(people_schemas, "REVIEW_CONCLUSIONS")


def test_the_agents_reviewed_is_the_status_and_nothing_else(client, db_session, test_project, test_user):
    from app.api.v1.endpoints.mcp_tools import TOOLS
    from app.main import app

    host = _host(db_session, test_project.id, "10.70.0.2")
    db_session.commit()
    headers = _agent_headers(client, test_project)

    r = client.post(f"/api/v1/agent/hosts/{host.id}/follow", headers=headers, json={"status": "reviewed"})
    assert r.status_code == 204, r.text
    row = _row(db_session, host, test_user)
    assert row.status == FollowStatus.REVIEWED and row.reviewed_at is not None
    assert row.review_conclusion is None and row.review_summary is None

    # The route's request, and the tool derived from it, take the host and
    # the status — no conclusion, no summary.
    assert set(app.openapi()["components"]["schemas"]["AgentFollowRequest"]["properties"]) == {"status"}
    assert set(TOOLS["assist_set_follow"]["input_schema"]["properties"]) == {"host_id", "status"}
    described = TOOLS["assist_set_follow"]["description"].lower()
    assert "conclusion" not in described and "evidence" not in described

    # Over MCP an argument the tool does not take is refused, not ignored.
    rpc = client.post("/api/v1/mcp", headers=headers, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "assist_set_follow",
                   "arguments": {"host_id": host.id, "status": "reviewed", "review_conclusion": "no_issue"}},
    }).json()
    assert rpc["error"]["code"] == -32602, rpc
    assert _row(db_session, host, test_user).review_conclusion is None


def test_a_conclusion_still_sent_is_never_written(client, db_session, test_project, test_user):
    """Over plain HTTP a body key the model does not know is ignored (as on
    every route here).  What matters is that it reaches no column — on the
    single route, the bulk one, or the agents'."""
    one, many, agent = (_host(db_session, test_project.id, f"10.70.1.{i}") for i in (1, 2, 3))
    db_session.commit()
    sent = {"status": "reviewed", "review_conclusion": "needs_evidence", "review_summary": "typed in the old dialog"}

    assert client.post(f"{_base(test_project)}/{one.id}/follow", json=sent).status_code == 200
    assert client.post(f"{_base(test_project)}/bulk/follow", json={**sent, "host_ids": [many.id]}).status_code == 200
    assert client.post(f"/api/v1/agent/hosts/{agent.id}/follow",
                       headers=_agent_headers(client, test_project), json=sent).status_code == 204

    for host in (one, many, agent):
        row = _row(db_session, host, test_user)
        assert row.status == FollowStatus.REVIEWED, host.ip_address
        assert row.review_conclusion is None and row.review_summary is None, host.ip_address


# ---------------------------------------------------------------------------
# A conclusion stored earlier
# ---------------------------------------------------------------------------

def test_a_stored_conclusion_survives_reviewed_again_and_goes_when_reopened(
    client, db_session, test_project, test_user,
):
    """Old rows carry a conclusion.  Marking such a host Reviewed again — the
    page, the bulk bar, an agent — never fails and leaves the conclusion and
    the note as they are (only the review's date moves).  Re-opening the
    review is the reviewer saying it no longer stands: both go, and a later
    Reviewed starts with none."""
    host = _host(db_session, test_project.id, "10.70.2.1")
    long_ago = datetime.now(timezone.utc) - timedelta(days=9)
    for stored in ("needs_evidence", "no_action"):          # `no_action` is older still
        db_session.query(HostFollow).filter_by(host_id=host.id).delete()
        db_session.add(HostFollow(
            host_id=host.id, user_id=test_user.id, status=FollowStatus.REVIEWED,
            review_conclusion=stored, review_summary="what the old dialog recorded", reviewed_at=long_ago,
        ))
        db_session.commit()

        # It is still read, as stored, on the host and on the list.
        detail = client.get(f"{_base(test_project)}/{host.id}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["follow"]["review_conclusion"] == stored
        assert detail.json()["follow"]["review_summary"] == "what the old dialog recorded"
        listed = client.get(f"{_base(test_project)}/").json()["items"]
        assert [i["follow"]["review_conclusion"] for i in listed if i["id"] == host.id] == [stored]

        doors = (
            lambda: client.post(f"{_base(test_project)}/{host.id}/follow", json={"status": "reviewed"}),
            lambda: client.post(f"{_base(test_project)}/bulk/follow",
                                json={"host_ids": [host.id], "status": "reviewed"}),
            lambda: client.post(f"/api/v1/agent/hosts/{host.id}/follow",
                                headers=_agent_headers(client, test_project), json={"status": "reviewed"}),
        )
        for again in doors:
            r = again()
            assert r.status_code in (200, 204), r.text
            row = _row(db_session, host, test_user)
            assert row.status == FollowStatus.REVIEWED
            assert row.review_conclusion == stored
            assert row.review_summary == "what the old dialog recorded"
            assert row.reviewed_at > long_ago

        # Back In review, then Reviewed: neither fails on the old row.
        r = client.post(f"{_base(test_project)}/{host.id}/follow", json={"status": "in_review"})
        assert r.status_code == 200, r.text
        row = _row(db_session, host, test_user)
        assert (row.review_conclusion, row.review_summary, row.reviewed_at) == (None, None, None)
        r = client.post(f"{_base(test_project)}/{host.id}/follow", json={"status": "reviewed"})
        assert r.status_code == 200, r.text
        assert r.json()["review_conclusion"] is None
        row = _row(db_session, host, test_user)
        assert row.review_conclusion is None and row.reviewed_at is not None


# ---------------------------------------------------------------------------
# The optional note
# ---------------------------------------------------------------------------

def test_the_review_note_is_its_own_call_and_moves_nothing_else(client, db_session, test_project, test_user):
    host = _host(db_session, test_project.id, "10.70.3.1")
    db_session.commit()
    url = f"{_base(test_project)}/{host.id}/follow"

    # A note is on a review: none yet, and not on one still open.
    assert client.patch(url, json={"review_summary": "too early"}).status_code == 409
    assert client.post(url, json={"status": "in_review"}).status_code == 200
    refused = client.patch(url, json={"review_summary": "too early"})
    assert refused.status_code == 409, refused.text
    assert "Reviewed" in refused.json()["detail"]
    assert _row(db_session, host, test_user).review_summary is None

    assert client.post(url, json={"status": "reviewed"}).status_code == 200
    row = _row(db_session, host, test_user)
    row.review_conclusion = "out_of_scope"                   # as an older review left it
    db_session.commit()
    reviewed_at = _row(db_session, host, test_user).reviewed_at

    r = client.patch(url, json={"review_summary": "  RDP only, patched in June  "})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "reviewed"
    assert r.json()["review_summary"] == "RDP only, patched in June"
    assert r.json()["review_conclusion"] == "out_of_scope"
    row = _row(db_session, host, test_user)
    assert row.review_summary == "RDP only, patched in June"
    # Not the date "changed since review" is measured against, not the state,
    # not the stored conclusion.
    assert row.reviewed_at == reviewed_at
    assert row.status == FollowStatus.REVIEWED and row.review_conclusion == "out_of_scope"
    assert client.get(f"{_base(test_project)}/{host.id}").json()["follow"]["review_summary"] == "RDP only, patched in June"

    # Blank and null both remove it; an over-long one is refused, not cut.
    for empty in ("   ", None):
        assert client.patch(url, json={"review_summary": "again"}).status_code == 200
        r = client.patch(url, json={"review_summary": empty})
        assert r.status_code == 200 and r.json()["review_summary"] is None, r.text
        assert _row(db_session, host, test_user).review_summary is None
    assert client.patch(url, json={"review_summary": "x" * 4001}).status_code == 422
    assert client.patch(url, json={"review_summary": "x" * 4000}).status_code == 200

    # Re-opening the review clears its note with it.
    assert client.post(url, json={"status": "in_review"}).status_code == 200
    assert _row(db_session, host, test_user).review_summary is None

    assert client.patch(f"{_base(test_project)}/999999/follow", json={"review_summary": "x"}).status_code == 404


def test_the_review_note_is_the_reviewers_own_and_a_writers(client, db_session, test_project, test_user):
    from app.api.v1.endpoints.auth import get_current_user
    from app.db.models_project import ProjectRole
    from app.main import app
    from tests.test_agent_role_route_matrix import _member

    host = _host(db_session, test_project.id, "10.70.3.2")
    db_session.commit()
    url = f"{_base(test_project)}/{host.id}/follow"
    assert client.post(url, json={"status": "reviewed"}).status_code == 200
    assert client.patch(url, json={"review_summary": "mine"}).status_code == 200

    analyst = _member(db_session, test_project, ProjectRole.ANALYST)
    viewer = _member(db_session, test_project, ProjectRole.VIEWER)
    mine = app.dependency_overrides[get_current_user]
    try:
        # A teammate has no review of this host: nothing of the reviewer's is
        # reachable through their call.
        app.dependency_overrides[get_current_user] = lambda: analyst
        assert client.patch(url, json={"review_summary": "not mine"}).status_code == 409
        assert client.post(url, json={"status": "reviewed"}).status_code == 200
        assert client.patch(url, json={"review_summary": "the analyst's"}).status_code == 200

        # A viewer may keep a review status (their own row, as before) but the
        # note is a write: analyst and up.
        app.dependency_overrides[get_current_user] = lambda: viewer
        assert client.post(url, json={"status": "reviewed"}).status_code == 200
        assert client.patch(url, json={"review_summary": "a viewer's"}).status_code == 403
    finally:
        app.dependency_overrides[get_current_user] = mine

    assert _row(db_session, host, test_user).review_summary == "mine"
    assert _row(db_session, host, analyst).review_summary == "the analyst's"
    assert _row(db_session, host, viewer).review_summary is None


# ---------------------------------------------------------------------------
# The bulk bar's Reviewed is the same write
# ---------------------------------------------------------------------------

def test_bulk_reviewed_is_the_single_write_per_host(client, db_session, test_project, test_user):
    """``POST /hosts/bulk/follow`` set the status alone: a host marked
    Reviewed from the bulk bar had no ``reviewed_at`` and could never be
    "changed since review".  It is the one write now — so such a host is
    listed when it changes, in the section and in ``follow:revisit`` alike."""
    fresh = [_host(db_session, test_project.id, f"10.70.4.{i}") for i in (1, 2)]
    reopened = _host(db_session, test_project.id, "10.70.4.3")
    db_session.add(HostFollow(
        host_id=reopened.id, user_id=test_user.id, status=FollowStatus.REVIEWED,
        review_conclusion="duplicate", review_summary="old", reviewed_at=datetime.now(timezone.utc),
    ))
    db_session.commit()

    r = client.post(f"{_base(test_project)}/bulk/follow",
                    json={"host_ids": [h.id for h in fresh], "status": "reviewed"})
    assert r.status_code == 200 and r.json()["affected"] == 2, r.text
    for host in fresh:
        row = _row(db_session, host, test_user)
        assert row.status == FollowStatus.REVIEWED
        assert row.reviewed_at is not None and row.assigned_at is not None
        assert row.assigned_by_id == test_user.id
        assert row.review_conclusion is None

    # In bulk too, leaving Reviewed clears what the review held.
    r = client.post(f"{_base(test_project)}/bulk/follow", json={"host_ids": [reopened.id], "status": "in_review"})
    assert r.status_code == 200, r.text
    row = _row(db_session, reopened, test_user)
    assert row.status == FollowStatus.IN_REVIEW
    assert (row.review_conclusion, row.review_summary, row.reviewed_at) == (None, None, None)

    # One of the bulk-reviewed hosts gains an open port afterwards.
    db_session.add(models.Port(host_id=fresh[0].id, port_number=8443, protocol="tcp", state="open",
                               first_seen=datetime.now(timezone.utc) + timedelta(hours=1)))
    db_session.commit()
    wb = f"/api/v1/projects/{test_project.id}/workbench"
    followups = client.get(wb).json()["followups"]
    revisit = client.get(f"{_base(test_project)}/", params={"q": "follow:revisit"}).json()
    assert [r["ip_address"] for r in followups["items"]] == ["10.70.4.1"]
    assert [i["ip_address"] for i in revisit["items"]] == ["10.70.4.1"]
    assert followups["total"] == revisit["total"] == 1
