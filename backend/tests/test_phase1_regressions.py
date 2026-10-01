"""Regression tests for the v2.15.0 (Phase 1) correctness fixes.

Each test pins a bug found in the backend code review so it can't
silently come back:

- B4  cross-project visibility — a record is readable only through its own
      project's URL

(B1, the recon upload attribution race, and B3, the recon port-overcount fix,
went with recon runs in v2.433.0.  B2, the sanity-check uniqueness key, and
every test here that pinned test plans, plan entries, execution runs, sanity
checks or the execution report went with those in v2.442.0; the ones whose
behaviour survives were rewritten against host tests and evidence records.)
"""
from __future__ import annotations

import uuid

import pytest


# ---------------------------------------------------------------------------
# Shared helpers: a host, a host test created through the real route, and an
# agent session + key minted through the one start endpoint.
# ---------------------------------------------------------------------------

def _mk_host(db_session, project_id: int, ip: str):
    from app.db import models
    host = models.Host(ip_address=ip, state="up", project_id=project_id)
    db_session.add(host)
    db_session.commit()
    db_session.refresh(host)
    return host


def _test_payload(host_id: int, **extra) -> dict:
    return dict(
        request_key=str(uuid.uuid4()), host_id=host_id, tool="nmap",
        description="Service detection", rationale="regression-test fixture",
        **extra,
    )


def _create_host_test(client, project_id: int, host_id: int, **extra) -> dict:
    resp = client.post(
        f"/api/v1/projects/{project_id}/host-tests",
        json={"tests": [_test_payload(host_id, **extra)]},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["items"][0]


# ---------------------------------------------------------------------------
# B4 — a record reached through the WRONG project's URL is a 404, never the
# other project's data.  (Pinned on test plans until v2.442.0, where the 404
# also named the owning project; a host test's 404 does not say where it is.)
# ---------------------------------------------------------------------------

def test_get_host_test_in_wrong_project_returns_404(client, db_session, test_project):
    from app.db.models_project import Project

    other = Project(name="other-project", slug="other-project", description="x")
    db_session.add(other)
    db_session.commit()
    db_session.refresh(other)
    host = _mk_host(db_session, other.id, "10.0.9.1")
    row = _create_host_test(client, other.id, host.id)

    # Ask for it — and try to change it — from the WRONG project's URL scope.
    url = f"/api/v1/projects/{test_project.id}/host-tests/{row['id']}"
    resp = client.get(url)
    assert resp.status_code == 404
    assert "Service detection" not in resp.text
    patched = client.patch(url, json={"expected_revision": row["revision"], "status": "in_progress"})
    assert patched.status_code == 404
    # Nor does the wrong project's list carry it.
    listed = client.get(f"/api/v1/projects/{test_project.id}/host-tests")
    assert listed.status_code == 200
    assert row["id"] not in [t["id"] for t in listed.json()["items"]]
    # It is untouched where it lives.
    own = client.get(f"/api/v1/projects/{other.id}/host-tests/{row['id']}")
    assert own.status_code == 200
    assert own.json()["status"] == "proposed"


def test_get_host_test_truly_missing_returns_plain_404(client, test_project):
    resp = client.get(f"/api/v1/projects/{test_project.id}/host-tests/999999")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Host test not found in this project"


def test_get_host_test_in_correct_project_succeeds(client, db_session, test_project):
    host = _mk_host(db_session, test_project.id, "10.0.9.2")
    row = _create_host_test(client, test_project.id, host.id)
    resp = client.get(f"/api/v1/projects/{test_project.id}/host-tests/{row['id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == row["id"]
    assert body["host_id"] == host.id


# ---------------------------------------------------------------------------
# Batch B regressions — SBOM cache invalidation.  (Its sanity-check
# enforcement, byte-cap truncation and brief-mode policy tests went with
# execution runs and plan context in v2.442.0; evidence output size is
# measured in bytes in test_agent_proposals.py.)
# ---------------------------------------------------------------------------

def test_sbom_cache_invalidates_on_app_version_change(monkeypatch, tmp_path):
    """#4: the cache used to key only on manifest mtimes, so an
    ``app_version`` bump that didn't touch requirements.txt /
    package-lock.json kept serving the stale envelope."""
    from app.services import sbom_service

    # Isolate from any cache the suite has primed and from the real
    # manifest paths (so we don't depend on whether package-lock is
    # mounted into the test container).
    monkeypatch.setattr(sbom_service, "_BACKEND_REQUIREMENTS_PATH", tmp_path / "requirements.txt")
    monkeypatch.setattr(sbom_service, "_FRONTEND_LOCK_PATH", tmp_path / "package-lock.json")
    monkeypatch.setattr(sbom_service, "_cache", None)
    monkeypatch.setattr(sbom_service, "_cache_key", None)

    a = sbom_service.get_sbom("a.b.c")
    assert a["app_version"] == "a.b.c"
    b = sbom_service.get_sbom("x.y.z")
    assert b["app_version"] == "x.y.z"


# ---------------------------------------------------------------------------
# An agent session + key, shared by the audit-log, rate-limit and
# activity-stamp regressions below.  Minted through the one start endpoint
# (``POST /projects/{id}/assist/start``) so the key, its session and the
# session's detail row are exactly what an operator's agent holds.  (Until
# v2.442.0 this was an execution run with a plan-scoped key.)
# ---------------------------------------------------------------------------

AGENT_READ = "/api/v1/agent/host-tests"


@pytest.fixture
def agent_session_with_key(client, db_session, test_project):
    """``{key, headers, agent, agent_session_id}`` for a live project session
    started by the ``client`` fixture's user."""
    from app.db.models_agent import Agent

    resp = client.post(f"/api/v1/projects/{test_project.id}/assist/start", json={})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return {
        "key": body["api_key"],
        "headers": {"X-API-Key": body["api_key"]},
        "agent": db_session.get(Agent, body["agent_id"]),
        "agent_session_id": body["agent_session_id"],
    }


def test_prompt_version_bumped_for_environment_probe():
    """PROMPT_VERSION must never fall below the v2.23.0 floor (1.10.0) so
    agents using older prompts can tell they're on the wrong version. (The
    probe that raised the floor has since been removed; the floor stays.)"""
    from app.services.agent_prompt_service import PROMPT_VERSION
    # v2.23.0 raised the floor to 1.10.0 — anything lower is stale.
    major, minor, _ = PROMPT_VERSION.split(".")
    assert (int(major), int(minor)) >= (1, 10), PROMPT_VERSION


# ---------------------------------------------------------------------------
# v2.24.0 — agent API call log.  Middleware writes one row per inbound
# /agent/* request that authenticated as an agent; the human-facing
# /projects/{id}/agent-sessions/{id}/api-activity endpoint serves them
# back so a user can audit "did the agent query the right hosts?".  (The
# per-plan api-activity endpoint these used went with plans in v2.442.0.)
# ---------------------------------------------------------------------------

def test_middleware_helpers_extract_host_ids_and_target_ips():
    """The cheap reference-extraction helpers are the heart of the
    "did the agent query the right hosts?" query.  Pin their behaviour."""
    from app.services.agent_api_log_service import (
        _coerce_int_list, _extract_target_ips, _collect_referenced_ids,
        _strip_sensitive_fields, _summarise_body,
    )

    # Comma-separated string of ids — both the path-param and query-
    # string call patterns rely on this.
    assert _coerce_int_list("1,2,3") == [1, 2, 3]
    assert _coerce_int_list("1, 2, 2, 3") == [1, 2, 3]
    assert _coerce_int_list(42) == [42]
    assert _coerce_int_list([1, "2", "x"]) == [1, 2]
    assert _coerce_int_list(None) == []

    # IP extraction from arbitrary nested input.
    assert _extract_target_ips({"target_ip": "10.0.0.5"}) == ["10.0.0.5"]
    assert _extract_target_ips({"a": ["10.0.0.5", "10.0.0.5", "10.0.0.6"]}) == [
        "10.0.0.5", "10.0.0.6",
    ]
    # Random strings don't get false-positive'd.
    assert "not-an-ip" not in _extract_target_ips({"x": "not-an-ip"})

    # Aggregated path + query + body extraction.
    host_ids, entry_ids, ips = _collect_referenced_ids(
        path_params={"entry_id": "7"},
        query_params={"host_ids": "1,2,3"},
        body_json={"target_ip": "10.0.0.5", "host_id": 4},
    )
    assert sorted(host_ids) == [1, 2, 3, 4]
    assert entry_ids == [7]
    assert ips == ["10.0.0.5"]

    # Sensitive fields are stripped from captured bodies (defence in
    # depth — agents never put their key in the body, but we strip
    # anyway).
    stripped = _strip_sensitive_fields({"api_key": "secret", "host_id": 5})
    assert stripped == {"api_key": "***", "host_id": 5}

    # Body summarisation honours the cap and the content type.
    big = b"x" * (10 * 1024)
    summarised = _summarise_body(big, "application/json")
    assert summarised["_truncated"] is True
    assert summarised["_size"] == len(big)
    # Multipart bodies skip payload capture entirely (file uploads).
    summarised = _summarise_body(b"x" * 10, "multipart/form-data; boundary=abc")
    assert summarised["_multipart"] is True


def _activity_url(project_id: int, agent_session_id: int) -> str:
    return f"/api/v1/projects/{project_id}/agent-sessions/{agent_session_id}/api-activity"


def test_middleware_records_agent_request_against_session(
    client, agent_session_with_key, db_session, test_project,
):
    """End-to-end: an agent calls GET /agent/host-tests with its key, the
    middleware writes one row, the human-facing list endpoint returns it.
    Verifies wiring + the cross-cut: a real request stamps agent_id,
    agent_session_id, project_id, response status, method, path, duration."""
    from app.db.models_agent import AgentApiCall
    s = agent_session_with_key

    resp = client.get(
        AGENT_READ, headers={**s["headers"], "User-Agent": "fixture-agent/1.0"},
    )
    assert resp.status_code == 200, resp.text

    # Middleware wrote one row.
    rows = (
        db_session.query(AgentApiCall)
        .filter(AgentApiCall.agent_session_id == s["agent_session_id"])
        .all()
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.method == "GET"
    assert row.path.endswith("/agent/host-tests")
    assert row.status_code == 200
    assert row.agent_id == s["agent"].id
    assert row.project_id == test_project.id
    assert row.user_agent == "fixture-agent/1.0"
    assert row.duration_ms is not None
    # Path-template captured for grouping.
    assert (row.path_template or "").endswith("/host-tests")

    # Human-facing endpoint surfaces it.
    list_resp = client.get(_activity_url(test_project.id, s["agent_session_id"]))
    assert list_resp.status_code == 200, list_resp.text
    body = list_resp.json()
    assert body["total"] == 1
    assert body["items"][0]["path_template"].endswith("/host-tests")


def test_middleware_captures_mutation_body_and_references_hosts(
    client, agent_session_with_key, db_session, test_project,
):
    """A POST (mutation) gets its body summarised and any host_id / IP
    references parsed out so the host and target filters on the activity
    endpoint work."""
    from app.db.models_agent import AgentApiCall
    s = agent_session_with_key
    host = _mk_host(db_session, test_project.id, "10.0.0.42")
    other = _mk_host(db_session, test_project.id, "10.0.0.43")

    resp = client.post(
        "/api/v1/agent/evidence",
        headers=s["headers"],
        json={
            "host_id": host.id, "tool": "nc", "command": "nc -v 10.0.0.42 22",
            "outcome": "info", "summary": "banner matched",
            "observed_ip": "10.0.0.42",
        },
    )
    assert resp.status_code == 201, resp.text

    row = (
        db_session.query(AgentApiCall)
        .filter(AgentApiCall.method == "POST",
                AgentApiCall.agent_session_id == s["agent_session_id"])
        .order_by(AgentApiCall.created_at.desc())
        .first()
    )
    assert row is not None
    # Body summary captured (mutation body, under the cap, JSON parsed).
    assert row.request_body_summary["tool"] == "nc"
    assert row.request_body_summary["observed_ip"] == "10.0.0.42"
    # Host-reference index populated from the body.
    assert row.referenced_host_ids == [host.id]
    assert "10.0.0.42" in (row.referenced_target_ips or [])

    url = _activity_url(test_project.id, s["agent_session_id"])
    # The target filter returns this row, and only rows naming that address.
    filtered = client.get(url, params={"target_ip": "10.0.0.42"})
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
    assert all(
        "10.0.0.42" in (item.get("referenced_target_ips") or [])
        for item in filtered.json()["items"]
    )
    # The host filter finds it by id, and a host the call never named does not.
    assert client.get(url, params={"host_id": host.id}).json()["total"] == 1
    assert client.get(url, params={"host_id": other.id}).json()["total"] == 0


def test_api_activity_endpoints_enforce_project_membership(
    client, agent_session_with_key, test_project, db_session,
):
    """Cross-tenant IDOR regression: the per-session api-activity list
    endpoint must authorise via project membership (get_current_project),
    not merely authenticate.  A non-admin user who is not a member of the
    project must get 403; previously they could read another tenant's agent
    audit log (target IPs, request bodies) using the path project_id alone.
    A member of the same project still gets 200.
    """
    from datetime import datetime, timezone
    from app.main import app
    from app.api.v1.endpoints.auth import get_current_user
    from app.db.models_auth import User, UserRole
    from app.db.models_project import ProjectMembership

    outsider = User(
        # Explicit high id: test_user hardcodes id=1 without advancing the
        # Postgres sequence, so an id-less insert would collide on users_pkey.
        id=4242,
        username="idor-outsider",
        email="idor-outsider@example.com",
        full_name="Outsider",
        hashed_password="x",
        role=UserRole.MEMBER,  # not admin → no membership bypass
        is_active=True,
        is_verified=True,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(outsider)
    db_session.commit()
    db_session.refresh(outsider)

    url = _activity_url(test_project.id, agent_session_with_key["agent_session_id"])
    app.dependency_overrides[get_current_user] = lambda: outsider

    # Non-member → 403.
    assert client.get(url).status_code == 403

    # Grant membership → the same user now passes the authz gate (200).
    db_session.add(ProjectMembership(
        project_id=test_project.id, user_id=outsider.id, role="viewer",
    ))
    db_session.commit()
    assert client.get(url).status_code == 200


def test_api_activity_owner_attribution_and_mine_filter(
    client, agent_session_with_key, test_project, db_session,
):
    """Each activity row carries owner/agent attribution (joined from
    Agent.owner), and ?mine=true restricts to the current user's own
    agents — so one operator's calls aren't lost in a project-wide
    firehose.  The client fixture authenticates as test-admin, who owns the
    session's agent; a second agent owned by another user must be excluded
    under mine=true but visible (attributed) in the default 'all' view."""
    from datetime import datetime, timezone
    from app.db.models_agent import Agent, AgentApiCall
    from app.db.models_auth import User, UserRole
    s = agent_session_with_key

    other = User(
        id=7777, username="other-owner", email="other-owner@example.com",
        hashed_password="x", role=UserRole.MEMBER, is_active=True,
        is_verified=True, created_at=datetime.now(timezone.utc),
    )
    db_session.add(other)
    db_session.flush()
    other_agent = Agent(
        name="other-agent", project_id=test_project.id,
        owner_id=other.id, is_active=True,
    )
    db_session.add(other_agent)
    db_session.flush()

    for ag in (s["agent"], other_agent):
        db_session.add(AgentApiCall(
            agent_id=ag.id, project_id=test_project.id,
            agent_session_id=s["agent_session_id"], method="GET", path="/x",
            status_code=200, duration_ms=1,
            created_at=datetime.now(timezone.utc),
        ))
    db_session.commit()

    url = _activity_url(test_project.id, s["agent_session_id"])

    # Default 'all' view: both rows, each attributed to its owner + agent.
    allr = client.get(url).json()
    assert allr["total"] == 2
    assert {i["owner_username"] for i in allr["items"]} == {"test-admin", "other-owner"}
    assert all(i["agent_name"] for i in allr["items"])

    # mine=true (caller is test-admin): only their own agent's row.
    mine = client.get(url, params={"mine": "true"}).json()
    assert mine["total"] == 1
    assert mine["items"][0]["owner_username"] == "test-admin"


def test_middleware_skips_unauthenticated_agent_requests(client, db_session):
    """A bad API key gets a 401 — and crucially produces NO log row.
    The audit log must only reflect successful agent attribution."""
    from app.db.models_agent import AgentApiCall
    before = db_session.query(AgentApiCall).count()
    resp = client.get(
        AGENT_READ, headers={"X-API-Key": "nm_agent_definitely_not_real_key"},
    )
    assert resp.status_code == 401
    db_session.expire_all()
    assert db_session.query(AgentApiCall).count() == before


def test_purge_older_than_drops_old_rows(db_session, test_agent, test_project):
    """The retention helper deletes rows older than the cutoff and
    returns the count.  Manually-aged rows simulate days-old activity
    so the test runs in milliseconds."""
    from datetime import datetime, timedelta, timezone
    from app.db.models_agent import AgentApiCall
    from app.services.agent_api_log_service import purge_older_than

    old = datetime.now(timezone.utc) - timedelta(days=120)
    new = datetime.now(timezone.utc) - timedelta(days=5)
    for ts in (old, old, new):
        db_session.add(AgentApiCall(
            agent_id=test_agent.id,
            project_id=test_project.id,
            method="GET", path="/api/v1/agent/host-tests",
            status_code=200, duration_ms=10,
            created_at=ts,
        ))
    db_session.commit()

    deleted = purge_older_than(db_session, days=90)
    assert deleted == 2
    remaining = (
        db_session.query(AgentApiCall)
        .filter(AgentApiCall.project_id == test_project.id)
        .count()
    )
    assert remaining == 1  # the 5-day-old row survives


def test_purge_runs_in_batches_until_nothing_old_is_left(db_session, test_user):
    """Review 2026-09-23 R10: the purge deletes in batches (one unbatched
    DELETE of a long backlog held its worker), and the same helper serves the
    opt-in audit-log retention."""
    from datetime import datetime, timedelta, timezone
    from app.db.models_auth import AuditLog
    from app.services.agent_api_log_service import purge_rows_older_than

    old = datetime.now(timezone.utc) - timedelta(days=400)
    for i in range(7):
        db_session.add(AuditLog(user_id=test_user.id, action="login_failed", timestamp=old))
    db_session.add(AuditLog(user_id=test_user.id, action="login_success", timestamp=datetime.now(timezone.utc)))
    db_session.commit()

    assert purge_rows_older_than(db_session, AuditLog, AuditLog.timestamp, 365, batch=3) == 7
    assert [a.action for a in db_session.query(AuditLog).filter_by(user_id=test_user.id)] == ["login_success"]


# ---------------------------------------------------------------------------
# v2.25.0 — review-driven hardening pass.  Each test pins a specific
# finding from the cross-functional code review so it can't drift back.
# ---------------------------------------------------------------------------

def test_host_test_update_rejects_unknown_status(
    client, db_session, test_project, agent_session_with_key,
):
    """Critical #2 (then on a plan entry's completion): a status was a bare
    string, so a typo or arbitrary value landed in the DB and disappeared
    from every query that only recognises the canonical states.  A host
    test's status is a strict enum on both surfaces; unknown values → 422
    and the row is unchanged."""
    from app.db.models_host_tests import HostTest
    host = _mk_host(db_session, test_project.id, "10.0.0.99")
    row = _create_host_test(client, test_project.id, host.id)
    body = {"expected_revision": row["revision"], "status": "definitely-not-a-real-status"}

    resp = client.patch(
        f"/api/v1/agent/host-tests/{row['id']}",
        headers=agent_session_with_key["headers"], json=body,
    )
    assert resp.status_code == 422, resp.text
    resp = client.patch(
        f"/api/v1/projects/{test_project.id}/host-tests/{row['id']}", json=body,
    )
    assert resp.status_code == 422, resp.text

    db_session.expire_all()
    stored = db_session.get(HostTest, row["id"])
    assert stored.status == "proposed"
    assert stored.revision == row["revision"]


# ---------------------------------------------------------------------------
# v2.26.0 — auth-path infrastructure cleanup.
# 1. Rate limiter enforced from shared state (global across workers).
# 2. Activity stamping debounced to once per 60s.
# (3, a plan entry's completion recording plan history, went with plan
#  history in v2.442.0.)
# ---------------------------------------------------------------------------

def test_rate_limit_is_enforced_from_shared_state_not_the_audit_log(
    client, db_session, test_project, agent_session_with_key,
):
    """v2.300.0 — the limit is enforced from ``agent_rate_buckets``.

    This replaces two tests that asserted the previous mechanism: a COUNT over
    ``agent_api_calls``. Those rows are written by a POST-RESPONSE background
    task, so the count excluded every in-flight request and read 0 if the
    writer was failing — the limiter failed open exactly under load. Enforcement
    no longer reads the audit log at all, so pre-loading it must NOT trip the
    limiter, and real requests must.
    """
    from datetime import datetime, timezone, timedelta
    from app.db.models_agent import AgentApiCall

    s = agent_session_with_key
    agent = s["agent"]
    agent.rate_limit_rpm = 3
    db_session.commit()

    # Audit rows at the limit. Under the old limiter this alone caused a 429.
    now = datetime.now(timezone.utc)
    for _ in range(5):
        db_session.add(AgentApiCall(
            agent_id=agent.id, project_id=test_project.id,
            agent_session_id=s["agent_session_id"],
            method="GET", path=AGENT_READ,
            status_code=200, duration_ms=10,
            created_at=now - timedelta(seconds=5),
        ))
    db_session.commit()

    # The audit log is not the limiter's input, so this passes.
    assert client.get(AGENT_READ, headers=s["headers"]).status_code == 200

    # Actual requests are what count. Two more reach the limit of 3...
    for _ in range(2):
        assert client.get(AGENT_READ, headers=s["headers"]).status_code == 200
    # ...and the next is refused.
    assert client.get(AGENT_READ, headers=s["headers"]).status_code == 429


def test_rate_limit_does_not_count_a_previous_window(
    client, db_session, agent_session_with_key,
):
    """A spent window must not hold the agent down forever — otherwise the
    count would only ever climb and the limit would become a permanent ban."""
    from datetime import datetime, timezone
    from app.db.models_agent import AgentRateBucket

    s = agent_session_with_key
    agent = s["agent"]
    agent.rate_limit_rpm = 3
    db_session.commit()

    # A full bucket belonging to an EARLIER window.
    now = datetime.now(timezone.utc)
    epoch = int(now.timestamp()) // 60 * 60
    previous = datetime.fromtimestamp(epoch - 60, tz=timezone.utc)
    db_session.add(AgentRateBucket(
        agent_id=agent.id, window_start=previous, count=999,
    ))
    db_session.commit()

    resp = client.get(AGENT_READ, headers=s["headers"])
    assert resp.status_code == 200, resp.text


def test_rate_limit_holds_across_independent_connections(db_session):
    """Enforcement lives in shared DB state, not in the calling session.

    Each call here runs on its OWN connection and its own Agent instance, so
    nothing but the ``agent_rate_buckets`` row carries the count between them —
    which is what lets one limit span four Uvicorn workers.

    Honest scope: this does NOT fail against the old limiter, because the old
    per-worker deque did catch a burst confined to one process, and pytest is
    one process. A genuine multi-process test would need spawned workers. The
    defect is isolated by ``test_rate_limit_is_enforced_from_shared_state_not_
    the_audit_log`` above, which does fail pre-fix; this one pins the mechanism
    the cross-worker property rests on.

    The fixtures live inside the test transaction and so are invisible to other
    connections; this commits its own minimal project/user/agent on an
    independent session and removes them afterwards.
    """
    from fastapi import HTTPException
    from sqlalchemy.orm import sessionmaker

    from app.api.deps import check_agent_rate_limit
    from app.db.models_agent import Agent, AgentRateBucket
    from app.db.models_auth import User, UserRole
    from app.db.models_project import Project
    from tests.conftest import engine

    if db_session.bind.dialect.name != "postgresql":
        pytest.skip("the ON CONFLICT upsert is the Postgres path")

    Independent = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    ids = {}
    try:
        with Independent() as s:
            project = Project(name="rate-limit-concurrency", slug="rate-limit-concurrency")
            s.add(project)
            user = User(
                username="rate-limit-worker",
                email="rate-limit-worker@example.com",
                hashed_password="x",
                role=UserRole.MEMBER,
                is_active=True,
            )
            s.add(user)
            s.flush()
            agent = Agent(
                name="rate-limit-agent", project_id=project.id,
                owner_id=user.id, rate_limit_rpm=3,
            )
            s.add(agent)
            s.commit()
            ids = {"project": project.id, "user": user.id, "agent": agent.id}

        admitted, refused = 0, 0
        # Six calls, each on its OWN connection — no shared process state.
        for _ in range(6):
            with Independent() as worker_db:
                worker_agent = worker_db.get(Agent, ids["agent"])
                try:
                    check_agent_rate_limit(agent=worker_agent, db=worker_db)
                    admitted += 1
                except HTTPException as exc:
                    assert exc.status_code == 429
                    refused += 1

        assert admitted == 3, (
            f"{admitted} calls admitted against a limit of 3 — the limit is not "
            "shared across connections"
        )
        assert refused == 3

        # The count is in the row, and refused calls are counted too — with a
        # fixed window that cannot extend a lockout past the window's own
        # expiry, so an attacker gets no uncounted retries.
        with Independent() as s:
            buckets = s.query(AgentRateBucket).filter(
                AgentRateBucket.agent_id == ids["agent"]
            ).all()
            assert len(buckets) == 1
            assert buckets[0].count == 6
    finally:
        with Independent() as s:
            if ids.get("agent"):
                s.query(AgentRateBucket).filter(
                    AgentRateBucket.agent_id == ids["agent"]
                ).delete(synchronize_session=False)
                s.query(Agent).filter(Agent.id == ids["agent"]).delete()
            if ids.get("project"):
                s.query(Project).filter(Project.id == ids["project"]).delete()
            if ids.get("user"):
                s.query(User).filter(User.id == ids["user"]).delete()
            s.commit()


def test_audit_log_redacts_value_shaped_secrets():
    """The by-value redaction path (_redact_secret_values) scrubs secrets
    that hide under a non-sensitive key — an agent key pasted into a recon
    command, a JWT, or a Bearer token in a free-text body.  The by-key
    stripper (_strip_sensitive_fields) cannot catch these, and the
    agent_api_calls table is surfaced to every project viewer, so a
    regression here would leak credentials to lower-privileged users."""
    from app.services.agent_api_log_service import (
        _redact_secret_values, _strip_sensitive_fields,
    )

    cmd = "curl -H 'X-API-Key: nm_agent_REALSECRETtoken123' https://h/x"
    assert "nm_agent_REALSECRETtoken123" not in _redact_secret_values(cmd)
    assert "***" in _redact_secret_values(cmd)

    jwt = "eyJhbGc.eyJzdWIiOiIxIn0.sigPART_abc-123"
    assert jwt not in _redact_secret_values(f"token={jwt}")

    assert "abc.def-TOKEN" not in _redact_secret_values(
        "Authorization: Bearer abc.def-TOKEN"
    )

    # And via the nested body walker: a secret under a benign key name
    # ("command") is still scrubbed because the walker redacts string values.
    cleaned = _strip_sensitive_fields({"command": cmd, "host_id": 5})
    assert "nm_agent_REALSECRETtoken123" not in cleaned["command"]
    assert cleaned["host_id"] == 5


def _key_row(db_session, raw_key: str):
    import hashlib
    from app.db.models_auth import APIKey
    return db_session.query(APIKey).filter(
        APIKey.key_hash == hashlib.sha256(raw_key.encode()).hexdigest()
    ).first()


def test_activity_stamp_debounced(client, db_session, agent_session_with_key):
    """v2.26.0 — last_used / last_activity_at only updates when the
    persisted value is older than the debounce window.  A request that
    follows a recent one must NOT advance the timestamp."""
    from datetime import datetime, timezone, timedelta
    s = agent_session_with_key
    agent = s["agent"]

    # Stamp both as "just used now" — well inside the debounce window.
    fresh = datetime.now(timezone.utc) - timedelta(seconds=5)
    api_key_row = _key_row(db_session, s["key"])
    api_key_row.last_used = fresh
    agent.last_activity_at = fresh
    db_session.commit()
    captured_key_ts = api_key_row.last_used
    captured_agent_ts = agent.last_activity_at

    # Drive a request that should NOT advance either timestamp.
    resp = client.get(AGENT_READ, headers=s["headers"])
    assert resp.status_code == 200, resp.text

    db_session.refresh(api_key_row)
    db_session.refresh(agent)
    assert api_key_row.last_used == captured_key_ts, (
        "api_key.last_used must NOT be re-stamped within the debounce window"
    )
    assert agent.last_activity_at == captured_agent_ts, (
        "agent.last_activity_at must NOT be re-stamped within the debounce window"
    )


def test_activity_stamp_writes_when_stale(client, db_session, agent_session_with_key):
    """Conversely, a stale (or null) timestamp must be advanced on the
    next request — otherwise the value is never written at all."""
    from datetime import datetime, timezone, timedelta
    s = agent_session_with_key
    agent = s["agent"]

    stale = datetime.now(timezone.utc) - timedelta(seconds=300)
    api_key_row = _key_row(db_session, s["key"])
    api_key_row.last_used = stale
    agent.last_activity_at = stale
    db_session.commit()

    resp = client.get(AGENT_READ, headers=s["headers"])
    assert resp.status_code == 200, resp.text

    db_session.refresh(api_key_row)
    db_session.refresh(agent)
    assert api_key_row.last_used > stale, (
        "api_key.last_used must be advanced once the persisted value is stale"
    )
    assert agent.last_activity_at > stale, (
        "agent.last_activity_at must be advanced once the persisted value is stale"
    )


# ---------------------------------------------------------------------------
# v2.27.0 — backend monolith extractions.  These tests pin the public
# surface of the new service / parser modules so a future code-move
# can't quietly drop one of the functions or change a signature.
# ---------------------------------------------------------------------------

def test_v2_27_0_host_query_module_surface():
    """host_query.py exposes the helpers the route file imports as
    aliases (build_filtered_host_query, apply_host_sorting,
    parse_subnets, make_correlated_subquery, escape_like).  Also
    exposes the SERVICE_PORT_MAPPINGS dict consumed by both the
    search path and the structured ``services=`` filter."""
    from app.services import host_query
    assert callable(host_query.build_filtered_host_query)
    assert callable(host_query.apply_host_sorting)
    assert callable(host_query.parse_subnets)
    assert callable(host_query.make_correlated_subquery)
    assert callable(host_query.escape_like)
    assert isinstance(host_query.SERVICE_PORT_MAPPINGS, dict)
    # The escape helper does NOT mangle plain values, and it DOES
    # escape SQL LIKE wildcards.
    assert host_query.escape_like("simple") == "simple"
    assert host_query.escape_like("100%") == "100\\%"
    assert host_query.escape_like("a_b") == "a\\_b"


def test_v2_27_0_host_serialization_module_surface():
    """host_serialization.py exposes the dict-builders the route file
    imports as aliases."""
    from app.services import host_serialization
    assert callable(host_serialization.build_vuln_summary)
    assert callable(host_serialization.serialize_host_base)
    assert callable(host_serialization.serialize_host_detail)
    assert callable(host_serialization.serialize_vulnerability)
    assert callable(host_serialization.vulnerability_sort_key)
    assert host_serialization.SEVERITY_ORDER["critical"] == 0
    assert host_serialization.SEVERITY_ORDER["unknown"] == 5


def test_v2_27_0_content_detection_module_surface():
    """parsers/content_detection.py exposes the looks_like_*
    predicates + is_nessus_sample, all as module-level functions
    (previously bound to IngestionService).  Pin the surface so an
    accidental rename or deletion is caught.

    v2.65.0 — added `looks_like_dns_csv` to the expected set; the
    DNS-CSV detector landed sometime between v2.27.0 and now and the
    test wasn't bumped, so this pin was reading as "test broken"
    rather than "drift caught".
    """
    from app.parsers import content_detection
    public = sorted(
        x for x in dir(content_detection)
        if x.startswith("looks_like_") or x == "is_nessus_sample"
    )
    assert public == [
        "is_nessus_sample",
        "looks_like_amass",
        "looks_like_bloodhound",
        "looks_like_dirbuster",
        "looks_like_dns_csv",
        "looks_like_dnsx",  # added v2.88.0 — dnsx JSON/JSONL (closes #44)
        "looks_like_eyewitness_csv",  # added v2.353.0 — CSV by header, not filename
        "looks_like_eyewitness_json",
        "looks_like_gnmap",
        "looks_like_masscan_json",
        "looks_like_masscan_list",
        "looks_like_masscan_xml",
        "looks_like_naabu",
        "looks_like_netexec",
        "looks_like_nikto",
        "looks_like_nmap_xml",  # added v2.45.1 — structural root-element check
        "looks_like_nuclei",  # added v2.411.0 — Nuclei results (template-id + info / matched-at)
        "looks_like_openvas",
        "looks_like_rdap",  # added v2.237.0 — RDAP network registration
        "looks_like_rustscan",
        "looks_like_smbmap",
    ]
    assert content_detection.looks_like_gnmap(b"# Nmap 7.94 scan initiated\nHost: 10.0.0.1")


# v2.45.1 — nmap XML mis-detected as OpenVAS when NSE captures
# openvas/greenbone strings (e.g. http-title or ssl-cert script
# output from a Greenbone-hosted scanner being scanned BY nmap).
# Operator hit this on a real engagement and had to sanitize their
# own scan output as a workaround.  Fix: detect by XML root element,
# not free-text body keywords.

# v2.49.5 — the v2.45.1 fixture used a contrived prolog order
# (decl -> DOCTYPE -> PI -> root, no comment) that happened to
# satisfy the original single-shot prolog regex.  Real ``nmap -oX``
# output is decl -> PI -> COMMENT -> root, and the comment broke
# the regex so detection fell through to the keyword fallback and
# mis-fired on greenbone/openvas substrings in NSE script output.
# The fixture now mirrors real nmap output so a future regex
# regression can't pass this test without also passing real files.
_NMAP_XML_WITH_OPENVAS_NSE = b"""<?xml version="1.0" encoding="UTF-8"?>
<?xml-stylesheet href="file:///usr/bin/../share/nmap/nmap.xsl" type="text/xsl"?>
<!-- Nmap 7.94SVN scan initiated Mon May 22 11:23:45 2026 as: nmap -sV -sC -oX scan.xml 10.32.56.11 -->
<nmaprun scanner="nmap" args="nmap -sV -sC 10.32.56.11" start="1715000000" version="7.94SVN">
  <host>
    <address addr="10.32.56.11" addrtype="ipv4"/>
    <ports>
      <port protocol="tcp" portid="443">
        <state state="open" reason="syn-ack"/>
        <service name="https" tunnel="ssl"/>
        <script id="http-title" output="OPENVAS Scan Report"/>
        <script id="ssl-cert" output="Subject: commonName=greenbone-host/organizationName=Greenbone AG"/>
      </port>
    </ports>
  </host>
</nmaprun>"""

_GENUINE_OPENVAS_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<report id="abc-123" extension="xml" content_type="text/xml" format_id="abc">
  <results count="3" filtered="2">
    <result id="r1">
      <name>Test finding</name>
      <threat>Medium</threat>
    </result>
  </results>
</report>"""

_LEGACY_OPENVAS_XML = b"""<?xml version="1.0"?>
<openvas-results>
  <result host="10.0.0.1">stuff</result>
</openvas-results>"""


def test_nmap_xml_with_openvas_in_nse_output_is_not_misdetected():
    """The bug: nmap XML mentioning openvas/greenbone in NSE script
    output got mis-routed to OpenVASParser.  After v2.45.1, the
    structural root-element check (``<nmaprun>``) wins over keyword
    matching.
    """
    from app.parsers import content_detection
    assert content_detection.looks_like_nmap_xml(_NMAP_XML_WITH_OPENVAS_NSE)
    assert not content_detection.looks_like_openvas(
        _NMAP_XML_WITH_OPENVAS_NSE, "nmap-output.xml"
    ), (
        "Regression: nmap XML with NSE-captured openvas/greenbone strings "
        "is being mis-detected as OpenVAS.  The root element is <nmaprun> "
        "— route to NmapXMLParser, not OpenVASParser."
    )


def test_genuine_openvas_xml_still_detected():
    """Defence-in-depth: tightening looks_like_openvas didn't break
    real OpenVAS XML detection."""
    from app.parsers import content_detection
    assert content_detection.looks_like_openvas(
        _GENUINE_OPENVAS_XML, "scan.xml"
    )
    assert not content_detection.looks_like_nmap_xml(_GENUINE_OPENVAS_XML)


def test_legacy_openvas_xml_still_detected():
    """Older OpenVAS exports use <openvas-results> as the root; the
    structural check covers them too."""
    from app.parsers import content_detection
    assert content_detection.looks_like_openvas(
        _LEGACY_OPENVAS_XML, "old-scan.xml"
    )


def test_xml_root_element_skips_real_nmap_prolog_with_comment():
    """v2.49.5 regression: the prolog skipper must handle the
    decl -> PI -> COMMENT -> root order that real ``nmap -oX``
    emits.  The v2.45.1 single-shot regex didn't, so detection fell
    through to the keyword fallback and re-opened the
    nmap-as-openvas bug.

    This test pins the structural behavior directly (independent of
    the higher-level detectors) so a future regex regression that
    drops comment-skipping cannot pass.
    """
    from app.parsers.content_detection import _xml_root_element
    real_nmap_prolog = b"""<?xml version="1.0" encoding="UTF-8"?>
<?xml-stylesheet href="file:///usr/bin/../share/nmap/nmap.xsl" type="text/xsl"?>
<!-- Nmap 7.94 scan initiated -->
<nmaprun scanner="nmap"></nmaprun>"""
    assert _xml_root_element(real_nmap_prolog) == "nmaprun", (
        "Prolog skipper must consume the <!-- Nmap ... --> comment "
        "that real ``nmap -oX`` writes before <nmaprun>."
    )

    # Defence-in-depth: also exercise BOM, multiple PIs, multiple
    # comments, and the legacy DOCTYPE-only prolog.
    bom_then_nmap = b"\xef\xbb\xbf<?xml version='1.0'?><nmaprun/>"
    assert _xml_root_element(bom_then_nmap) == "nmaprun", "BOM must be stripped before prolog matching."

    multi_prolog = b"""<?xml version="1.0"?>
<?xml-stylesheet href="a.xsl" type="text/xsl"?>
<?xml-stylesheet href="b.xsl" type="text/xsl"?>
<!-- comment 1 -->
<!-- comment 2 -->
<nmaprun/>"""
    assert _xml_root_element(multi_prolog) == "nmaprun", (
        "Prolog skipper must accept any number of PIs and comments in any order."
    )

    legacy_doctype = b"""<?xml version="1.0"?>
<!DOCTYPE nmaprun>
<nmaprun/>"""
    assert _xml_root_element(legacy_doctype) == "nmaprun"


def test_openvas_filename_still_wins_for_explicit_routing():
    """Operators who name files with explicit vendor keywords keep
    explicit routing — even when the body would otherwise not match
    a known XML shape."""
    from app.parsers import content_detection
    nondescript = b"<?xml version='1.0'?><something/>"
    assert content_detection.looks_like_openvas(nondescript, "openvas-scan.xml")
    assert content_detection.looks_like_openvas(nondescript, "greenbone-export.xml")
    assert content_detection.looks_like_openvas(nondescript, "gvm-report.xml")


def test_ingestion_dispatcher_puts_nmap_first_for_nmap_root():
    """Dispatcher integration: when the XML root is <nmaprun>,
    NmapXMLParser is the FIRST parser attempted regardless of
    keyword content elsewhere in the file."""
    # Stubbed job with the bug-trigger file content.
    from unittest.mock import MagicMock
    from app.services.ingestion_service import IngestionService
    job = MagicMock(original_filename="some-scan.xml")

    svc = IngestionService.__new__(IngestionService)  # bypass __init__
    attempts = list(svc._build_parsing_attempts(job, _NMAP_XML_WITH_OPENVAS_NSE))
    # First entry must be nmap_xml — pre-fix it was openvas_xml.
    assert attempts[0][0] == "nmap_xml", (
        f"Expected nmap_xml first; got {[a[0] for a in attempts]}.  "
        f"Regression: dispatcher is still putting OpenVASParser ahead "
        f"of NmapXMLParser when NSE script output mentions openvas/greenbone."
    )


# (v2.28.0's execution results panel and multi-execution comparison tests —
# per-entry execution results, latest_execution_session on the plan detail,
# the /feedback test_plan_id filter, the per-plan run list — went with test
# plans and execution runs in v2.442.0.)

# ---------------------------------------------------------------------------
# v2.28.1 — Nessus upload regression.  The v2.22.0 parse-stats plumbing
# initialised ``parse_stats`` only in the generic-parser branch of
# _execute_parser; the Nessus branch fell through to the shared
# return block and crashed with UnboundLocalError.  Pin the fix here.
# ---------------------------------------------------------------------------

def test_execute_parser_nessus_path_initialises_parse_stats(db_session, test_project):
    """The Nessus branch of _execute_parser must not crash with
    UnboundLocalError.  Mock NessusIntegrationService.process_nessus_file
    so we exercise the branch without needing a fixture .nessus file."""
    from unittest.mock import patch
    from app.services.ingestion_service import IngestionService
    from app.services.nessus_integration_service import NessusIntegrationService
    from app.db.models import IngestionJob

    svc = IngestionService()
    job = IngestionJob(
        filename="scan.nessus",
        original_filename="scan.nessus",
        storage_path="/tmp/does-not-matter.nessus",
        status="processing",
        options={"project_id": test_project.id},
        project_id=test_project.id,
    )
    db_session.add(job)
    db_session.commit()

    def _fake_process_nessus_file(self, storage_path, filename, project_id=None, **kwargs):
        # **kwargs: the dispatcher also passes skip_informational (v2.341.0).
        return {
            "success": True,
            "scan_id": 42,
            "message": "Nessus processed (mocked)",
        }

    with patch.object(
        NessusIntegrationService, "process_nessus_file",
        new=_fake_process_nessus_file,
    ):
        result = svc._execute_parser(
            db=db_session,
            job=job,
            parser_class=NessusIntegrationService,
            description="Nessus XML",
        )

    # Pre-fix: this would have raised UnboundLocalError before reaching
    # the assertion.  Post-fix: parse_stats defaults to an empty dict,
    # so the call returns the canonical shape with the two ingest-
    # quality columns at their zero floor.
    assert result["scan_id"] == 42
    assert result["tool_name"] == "Nessus"
    assert result["skipped_count"] == 0
    assert result["parser_warnings"] is None


# ---------------------------------------------------------------------------
# v2.28.2 — looks_like_httpx must accept bytes.  Pre-fix it was typed
# str but called with bytes from IngestionService._read_sample; every
# non-httpx .json upload crashed with TypeError on `startswith("{")`
# during dispatch, taking down amass/bloodhound/ffuf/naabu/feroxbuster/
# eyewitness/masscan/nikto/netexec/smbmap JSON uploads.
# ---------------------------------------------------------------------------

def test_looks_like_httpx_accepts_bytes():
    """The detector must work on bytes (production caller) without
    TypeError, and must STILL detect real httpx output by signature."""
    from app.parsers.httpx_parser import looks_like_httpx

    real_httpx = (
        b'{"timestamp":"2026-05-15T00:00:00Z","url":"https://10.0.1.5/",'
        b'"port":"443","status_code":200,"tech":["Nginx"],'
        b'"webserver":"nginx/1.18.0"}'
    )
    assert looks_like_httpx(real_httpx, "scan.jsonl") is True

    # Bytes input that's NOT httpx (an eyewitness JSON sample) must
    # return False, not crash.  Pre-fix this is where the production
    # bug fired.
    non_httpx_json = b'{"results": [{"url": "https://x/", "screenshot": "a.png"}]}'
    assert looks_like_httpx(non_httpx_json, "eyewitness_sample.json") is False


def test_looks_like_httpx_handles_arbitrary_non_httpx_bytes_without_crashing():
    """The bulk-upload regression: every non-httpx .json file was
    routed through this sniffer first.  Any TypeError here cascades
    into a parse failure for files that aren't even meant for httpx.
    Spot-check the formats seen failing in the user's bulk upload."""
    from app.parsers.httpx_parser import looks_like_httpx

    samples = {
        "amass_sample.json": b'[{"name":"a.example.com","domain":"example.com"}]',
        "bloodhound_sample.json": b'{"computers": [{"Name": "WS01"}]}',
        "ffuf_sample.json": b'{"results":[{"url":"http://x/","status":200}]}',
        "naabu_sample.json": b'{"ip":"10.0.1.5","port":443,"host":"x"}',
        "masscan_sample.json": b'{"ip":"10.0.1.5","ports":[{"port":443}]}',
        "nikto_sample.json": b'{"vulnerabilities":[{"id":"X","msg":"y"}]}',
    }
    for fname, blob in samples.items():
        # Must return a bool, NEVER raise.
        result = looks_like_httpx(blob, fname)
        assert isinstance(result, bool), f"non-bool from {fname!r}: {result!r}"


def test_looks_like_httpx_still_accepts_str_for_back_compat():
    """The old type annotation was ``str``; we accept both so any
    test or caller still passing str continues to work."""
    from app.parsers.httpx_parser import looks_like_httpx
    text_sample = (
        '{"url":"https://10.0.1.5/","status_code":200,"tech":["Nginx"],'
        '"webserver":"nginx"}'
    )
    assert looks_like_httpx(text_sample, "scan.jsonl") is True


# ---------------------------------------------------------------------------
# v2.30.0 — symmetric attribution + unified agent_sessions timeline.
# (The timeline's rows were recon / plan-generation / execution runs until
# the consolidation; they are project sessions plus legacy assist rows now.)
# ---------------------------------------------------------------------------

def _mk_agent_session(db_session, project_id, agent, *, model=None, tool=None,
                      status="active", hours_ago=1):
    from datetime import datetime, timezone, timedelta
    from app.db.models_agent import AgentSession, AgentSessionWorkflow
    row = AgentSession(
        workflow=AgentSessionWorkflow.PROJECT.value, project_id=project_id,
        agent_id=agent.id, started_by_id=agent.owner_id, status=status,
        started_at=datetime.now(timezone.utc) - timedelta(hours=hours_ago),
        generated_by_model=model, generated_by_tool=tool,
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


def test_agent_sessions_unified_timeline(
    client, db_session, test_project, test_agent,
):
    """The unified /agent-sessions endpoint returns project sessions and
    legacy assist rows in one timeline ordered newest-first, each carrying
    its attribution and the work it left behind (the host tests it proposed
    and the evidence it recorded)."""
    from app.db.models_agent import AgentSession
    from app.db.models_host_tests import HostTest
    from app.db.models_proposals import EvidenceRecord

    older = _mk_agent_session(
        db_session, test_project.id, test_agent,
        model="claude-opus-4-7", tool="claude-code", hours_ago=3,
    )
    newer = _mk_agent_session(
        db_session, test_project.id, test_agent,
        model="gpt-5-codex", tool="codex", hours_ago=1,
    )
    # A pre-consolidation assist session.
    db_session.add(AgentSession(
        workflow="assist", project_id=test_project.id, agent_id=test_agent.id,
        started_by_id=test_agent.owner_id, status="ended",
    ))
    host = _mk_host(db_session, test_project.id, "10.0.3.1")
    for i in range(2):
        db_session.add(HostTest(
            project_id=test_project.id, host_id=host.id, description=f"t{i}",
            rationale="fixture", priority="medium", status="proposed",
            source="agent", agent_session_id=newer.id,
            request_key=f"timeline-{i}", request_hash="x" * 64,
        ))
    db_session.add(EvidenceRecord(
        project_id=test_project.id, host_id=host.id, tool="nmap",
        outcome="no_finding", summary="nothing", agent_session_id=newer.id,
    ))
    db_session.commit()

    resp = client.get(f"/api/v1/projects/{test_project.id}/agent-sessions")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 3
    kinds = [s["kind"] for s in body["sessions"]]
    assert kinds.count("project") == 2
    assert "assist" in kinds
    projects = [s for s in body["sessions"] if s["kind"] == "project"]
    # Newest-started first.
    assert [s["id"] for s in projects] == [newer.id, older.id]
    assert projects[0]["generated_by_model"] == "gpt-5-codex"
    assert projects[0]["generated_by_tool"] == "codex"
    assert projects[0]["host_test_count"] == 2
    assert projects[0]["evidence_count"] == 1
    assert projects[1]["host_test_count"] == 0
    assert projects[1]["evidence_count"] == 0


def test_agent_sessions_filter_by_model(
    client, db_session, test_project, test_agent,
):
    """``?model=...`` narrows to sessions attributed to one model.
    Critical for the "compare runs by model" workflow."""
    _mk_agent_session(db_session, test_project.id, test_agent,
                      model="claude-opus-4-7", tool="claude-code", status="ended", hours_ago=2)
    codex = _mk_agent_session(db_session, test_project.id, test_agent,
                              model="gpt-5-codex", tool="codex", status="ended", hours_ago=1)
    # And one with no attribution, which must fall out of the filter.
    _mk_agent_session(db_session, test_project.id, test_agent, hours_ago=4)

    resp = client.get(
        f"/api/v1/projects/{test_project.id}/agent-sessions"
        "?model=gpt-5-codex"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    assert len(body["sessions"]) == 1
    assert body["sessions"][0]["id"] == codex.id
    assert body["sessions"][0]["generated_by_model"] == "gpt-5-codex"
    assert body["sessions"][0]["kind"] == "project"


def test_agent_sessions_by_model_tool_summary(
    client, db_session, test_project, test_agent,
):
    """The summary endpoint groups by (model, tool) and counts kinds.
    Drives the "compare models on this project" rollup card."""
    # Two claude-opus sessions, one codex session.
    for model_id, tool_id in [
        ("claude-opus-4-7", "claude-code"),
        ("claude-opus-4-7", "claude-code"),
        ("gpt-5-codex", "codex"),
    ]:
        _mk_agent_session(db_session, test_project.id, test_agent,
                          model=model_id, tool=tool_id, status="ended")

    resp = client.get(
        f"/api/v1/projects/{test_project.id}/agent-sessions/by-model-tool"
    )
    assert resp.status_code == 200, resp.text
    summary = {
        (r["generated_by_model"], r["generated_by_tool"]): r
        for r in resp.json()["summary"]
    }
    claude = summary[("claude-opus-4-7", "claude-code")]
    assert claude["project"] == 2
    assert claude["total"] == 2
    codex = summary[("gpt-5-codex", "codex")]
    assert codex["project"] == 1
    assert codex["total"] == 1
    # The retired kinds are not reported at all.
    assert "execution" not in claude and "plan_generation" not in claude


# ---------------------------------------------------------------------------
# v3 alpha.3 — status filter on /agent-sessions + project coverage endpoint.
# ---------------------------------------------------------------------------

def test_agent_sessions_status_filter_narrows_to_active(
    client, db_session, test_project, test_agent,
):
    """``?status=active`` returns only active sessions.  Drives the
    in-flight banner."""
    live = _mk_agent_session(db_session, test_project.id, test_agent, status="active")
    _mk_agent_session(db_session, test_project.id, test_agent, status="ended", hours_ago=2)

    resp = client.get(
        f"/api/v1/projects/{test_project.id}/agent-sessions?status=active"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    assert [s["id"] for s in body["sessions"]] == [live.id]
    assert all(s["status"] == "active" for s in body["sessions"])


def test_coverage_summary_counts_hosts_by_pipeline_stage(
    client, db_session, test_project,
):
    """Project coverage reports per-stage host counts and gap counts.

    Planned = the host has a test still to do (proposed / in progress);
    tested = the host has an evidence record whose outcome is finding,
    no_finding or inconclusive.  A finished or dismissed test does not keep
    a host planned, and a failed attempt or an informational record does
    not make it tested.  (``hosts_with_plan_entry`` /
    ``hosts_with_execution_result`` keep their names from test plans.)
    """
    from app.db.models_host_tests import HostTest
    from app.db.models_proposals import EvidenceRecord

    hosts = [
        _mk_host(db_session, test_project.id, f"10.0.1.{10 + i}") for i in range(6)
    ]

    def _test(host, status, key):
        db_session.add(HostTest(
            project_id=test_project.id, host_id=host.id, description="cov",
            rationale="cov", priority="medium", status=status, source="person",
            request_key=key, request_hash="x" * 64,
            dismissed_reason="descoped" if status == "dismissed" else None,
        ))

    def _evidence(host, outcome):
        db_session.add(EvidenceRecord(
            project_id=test_project.id, host_id=host.id, tool="nmap",
            outcome=outcome, summary="cov",
        ))

    _test(hosts[0], "proposed", "cov-0")          # planned
    _test(hosts[1], "in_progress", "cov-1")       # planned…
    _evidence(hosts[1], "no_finding")             # …and tested
    _test(hosts[2], "done", "cov-2")              # not planned any more
    _evidence(hosts[2], "inconclusive")           # tested
    _test(hosts[3], "dismissed", "cov-3")         # never planned
    _evidence(hosts[4], "failed")                 # an attempt is not a test
    _evidence(hosts[4], "info")
    # hosts[5] has neither (universe baseline).
    db_session.commit()

    resp = client.get(f"/api/v1/projects/{test_project.id}/coverage/")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_hosts"] == 6
    assert body["hosts_with_plan_entry"] == 2
    assert body["hosts_with_execution_result"] == 2
    assert body["hosts_no_plan"] == 4
    assert body["hosts_no_execution"] == 4


def test_coverage_summary_reports_scope_breakdown(
    client, db_session, test_project,
):
    """The coverage endpoint includes a per-scope row with
    discovered-vs-scoped counts so the v3 Operations page can render
    the scope-by-scope coverage list directly."""
    from app.db import models

    scope = models.Scope(
        name="cov-scope", description="x", project_id=test_project.id,
    )
    db_session.add(scope)
    db_session.flush()
    subnet = models.Subnet(
        scope_id=scope.id, cidr="10.99.0.0/30", description="four-IP range",
    )
    db_session.add(subnet)
    db_session.flush()

    # Two of the four IPs in the /30 are discovered.
    h1 = models.Host(
        ip_address="10.99.0.1", state="up", project_id=test_project.id,
    )
    h2 = models.Host(
        ip_address="10.99.0.2", state="up", project_id=test_project.id,
    )
    db_session.add(h1)
    db_session.add(h2)
    db_session.flush()
    db_session.add(models.HostSubnetMapping(host_id=h1.id, subnet_id=subnet.id))
    db_session.add(models.HostSubnetMapping(host_id=h2.id, subnet_id=subnet.id))
    db_session.commit()

    resp = client.get(f"/api/v1/projects/{test_project.id}/coverage/")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_scopes"] == 1
    sc = body["scopes"][0]
    assert sc["scope_name"] == "cov-scope"
    assert sc["total_scoped_ips"] == 4  # /30 → 4 addresses
    assert sc["discovered_in_scope"] == 2
    assert sc["coverage_percent"] == 50.0


# (v3 alpha.7's execution-session lookup, alpha.9's host workflow lineage and
# alpha.12's execution-session list went with execution runs in v2.442.0.)
