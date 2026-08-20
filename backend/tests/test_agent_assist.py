"""
Smoke tests for the v2.64.0 agent-assist workflow.

Covers the four invariants the workflow boundary depends on:

  1. JWT user can start a session and receives a working API key.
  2. The assist key can read /agent/assist/* endpoints (project-
     scoped, read-only data).
  3. The assist key is rejected by /agent/test-plans/* and
     /agent/recon/*, and by writes on /agent/hosts/* — even though
     the underlying `Agent` row is the same as for the other
     workflows.
  4. Ending the session moves it to 'ended' and the key stops working
     (next request returns 410 from the active-status guard).
"""

from datetime import datetime, timezone

import json
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _start_session(client, project_id, purpose="Smoke-test assist v1"):
    resp = client.post(
        f"/api/v1/projects/{project_id}/assist/start",
        json={"purpose": purpose},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _auth_headers(api_key: str) -> dict[str, str]:
    return {"X-API-Key": api_key}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_start_assist_session_returns_key_and_instructions(client, test_project):
    body = _start_session(client, test_project.id)
    assert body["project_id"] == test_project.id
    assert body["assist_session_id"] > 0
    assert body["api_key"].startswith("nm_agent_")
    # Instructions must mention the assist surface so a misrouted
    # paste doesn't accidentally drive a recon/plan agent.
    assert "/agent/assist/" in body["instructions"]
    # And the prompt must surface the session id so the agent
    # POSTs the env probe to the right path.
    assert str(body["assist_session_id"]) in body["instructions"]


def test_start_assist_populates_unified_agent_session(client, test_project, db_session):
    """R5 expand-completion: starting an assist session now also creates the
    unified AgentSession base row and links both the detail row and the minted
    key to it (was left null for the backfill migration)."""
    from app.db.models_agent import AssistSession, AgentSession, AgentSessionWorkflow
    from app.db.models_auth import APIKey

    body = _start_session(client, test_project.id)
    sid = body["assist_session_id"]

    detail = db_session.query(AssistSession).filter(AssistSession.id == sid).first()
    assert detail.agent_session_id is not None

    base = db_session.query(AgentSession).filter(AgentSession.id == detail.agent_session_id).first()
    assert base is not None
    assert base.workflow == AgentSessionWorkflow.ASSIST.value
    assert base.project_id == test_project.id

    key = (
        db_session.query(APIKey)
        .filter(APIKey.assist_session_id == sid, APIKey.is_active.is_(True))
        .first()
    )
    assert key.agent_session_id == base.id


def test_assist_key_can_read_context_and_hosts(client, test_project):
    body = _start_session(client, test_project.id)
    headers = _auth_headers(body["api_key"])

    # Context endpoint — should return project summary.
    ctx = client.get("/api/v1/agent/assist/context", headers=headers)
    assert ctx.status_code == 200, ctx.text
    data = ctx.json()
    assert data["project"]["id"] == test_project.id
    assert data["session"]["id"] == body["assist_session_id"]

    # Hosts endpoint — empty for a project with no hosts, but must not 401/403.
    hosts = client.get("/api/v1/agent/assist/hosts", headers=headers)
    assert hosts.status_code == 200, hosts.text
    assert isinstance(hosts.json(), list)


def test_assist_key_blocked_from_plan_and_recon_surfaces(client, test_project, test_plan):
    """The bedrock workflow-boundary guarantee: an assist key can't
    masquerade as a plan/recon key.  Both rejections come from the
    require_plan_scope / require_recon_scope deps explicitly checking
    request.state.scoped_assist_session_id.
    """
    body = _start_session(client, test_project.id)
    headers = _auth_headers(body["api_key"])

    # Plan endpoint — should 403 (not 401), reasoned by scope mismatch.
    plan = client.get(
        f"/api/v1/agent/test-plans/{test_plan.id}/context",
        headers=headers,
    )
    assert plan.status_code == 403, plan.text
    assert "assist" in plan.json()["detail"].lower()

    # Recon endpoint — same shape.
    recon = client.get("/api/v1/agent/recon/context", headers=headers)
    assert recon.status_code == 403, recon.text
    assert "assist" in recon.json()["detail"].lower()


def test_assist_key_cannot_write_notes_or_follow(client, test_project, db_session):
    """v1 assist is strictly read-only.  The two writes on agent_browse
    (notes + follow) must 403 even though the assist key authenticates
    as the agent the same way a plan/recon key does."""
    from app.db.models import Host
    host = Host(
        project_id=test_project.id,
        ip_address="10.99.0.1",
        state="up",
        first_seen=datetime.now(timezone.utc),
        last_seen=datetime.now(timezone.utc),
    )
    db_session.add(host)
    db_session.commit()
    db_session.refresh(host)

    body = _start_session(client, test_project.id)
    headers = _auth_headers(body["api_key"])

    note = client.post(
        f"/api/v1/agent/hosts/{host.id}/notes",
        headers=headers,
        json={"body": "should be rejected", "status": "info"},
    )
    assert note.status_code == 403, note.text
    assert "read-only" in note.json()["detail"].lower()

    follow = client.post(
        f"/api/v1/agent/hosts/{host.id}/follow",
        headers=headers,
        json={"status": "watching"},
    )
    assert follow.status_code == 403, follow.text
    assert "read-only" in follow.json()["detail"].lower()


def test_end_session_revokes_key(client, test_project):
    body = _start_session(client, test_project.id)
    headers = _auth_headers(body["api_key"])

    # Sanity: key works before end.
    pre = client.get("/api/v1/agent/assist/context", headers=headers)
    assert pre.status_code == 200, pre.text

    # End the session via the JWT-side endpoint.
    end = client.post(
        f"/api/v1/projects/{test_project.id}/assist/sessions/"
        f"{body['assist_session_id']}/end"
    )
    assert end.status_code == 204, end.text

    # Key should now be rejected — the API key row was deactivated, so
    # get_current_agent's `is_active.is_(True)` filter no longer matches.
    post = client.get("/api/v1/agent/assist/context", headers=headers)
    assert post.status_code == 401, post.text

    # Second end is idempotent in spirit but reports 409.
    second = client.post(
        f"/api/v1/projects/{test_project.id}/assist/sessions/"
        f"{body['assist_session_id']}/end"
    )
    assert second.status_code == 409, second.text


def test_environment_probe_returns_valid_response(client, test_project):
    """v2.64.1 regression — the initial v2.64.0 commit omitted
    session_type from EnvironmentProbeResponse, so Pydantic 500'd
    the response AFTER the DB write committed.  An agent saw a
    confusing 500 and retried, polluting the audit log.  Guard against
    a future schema bump that breaks the response again.
    """
    body = _start_session(client, test_project.id)
    headers = _auth_headers(body["api_key"])

    # Minimal valid EnvironmentProbeRequest — os_family is the only
    # required field (everything else is shaped for richer probes).
    resp = client.post(
        f"/api/v1/agent/assist/sessions/{body['assist_session_id']}/environment",
        headers=headers,
        json={
            "os_family": "linux",
            "agent_model": "gpt-5",
            "agent_tool": "codex",
            "agent_prompt_version": "1.44.0",
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["session_id"] == body["assist_session_id"]
    assert data["session_type"] == "assist"
    assert data["probed_at"] is not None
    # environment echo back — empty input round-trips to an empty
    # EnvironmentSummary, not a 500.
    assert isinstance(data["environment"], dict)
    # Agent feedback (v1.44.0): the three required attribution fields must be
    # echoed so the agent can verify they persisted (they land on separate
    # session columns and were previously dropped from the response).
    assert data["agent_model"] == "gpt-5"
    assert data["agent_tool"] == "codex"
    assert data["agent_prompt_version"] == "1.44.0"


def test_context_totals_includes_scan_count(client, test_project):
    body = _start_session(client, test_project.id)
    headers = _auth_headers(body["api_key"])
    ctx = client.get("/api/v1/agent/assist/context", headers=headers)
    assert ctx.status_code == 200, ctx.text
    # Agent feedback (v1.44.0): totals is the documented authoritative count
    # source but omitted scan_count.
    assert "scan_count" in ctx.json()["totals"]


def test_assist_session_exposes_capabilities_and_operator(client, test_project):
    # Read-only session: no write capabilities, but the bound operator is shown.
    ro = _start_session(client, test_project.id)
    ro_sess = client.get(
        "/api/v1/agent/assist/session", headers=_auth_headers(ro["api_key"])
    )
    assert ro_sess.status_code == 200, ro_sess.text
    d = ro_sess.json()
    assert d["capabilities"] == []            # read-only
    assert d["capability_constraint"] is None
    assert d["operator"] is not None and d["operator"]["id"] is not None

    # Write session: capabilities granted, row-scope constraint present.
    rw = client.post(
        f"/api/v1/projects/{test_project.id}/assist/start",
        json={"purpose": "write session", "can_write_assigned": True},
    )
    assert rw.status_code == 201, rw.text
    rw_sess = client.get(
        "/api/v1/agent/assist/session", headers=_auth_headers(rw.json()["api_key"])
    ).json()
    assert rw_sess["capabilities"], "write session should enumerate capabilities"
    assert rw_sess["capability_constraint"] == "assigned"


def test_assist_host_dto_carries_operator_follow_field(client, test_project, db_session):
    from app.db import models
    host = models.Host(ip_address="10.7.7.7", state="up", project_id=test_project.id)
    db_session.add(host)
    db_session.commit()
    body = _start_session(client, test_project.id)
    headers = _auth_headers(body["api_key"])
    listing = client.get("/api/v1/agent/assist/hosts", headers=headers)
    assert listing.status_code == 200, listing.text
    rows = listing.json()
    assert rows and "follow" in rows[0]  # present (null when the operator doesn't follow)
    detail = client.get(f"/api/v1/agent/assist/hosts/{host.id}", headers=headers)
    assert detail.status_code == 200
    assert "follow" in detail.json()


def test_write_session_prompt_has_no_read_only_contradiction(client, test_project):
    """The 'What this session can NOT do' block used to hard-code
    'cannot create notes / change follow — read-only' even when writes were
    granted, contradicting the Writing section."""
    rw = client.post(
        f"/api/v1/projects/{test_project.id}/assist/start",
        json={"purpose": "write session", "can_write_assigned": True},
    )
    instructions = rw.json()["instructions"]
    assert "strictly read-only" not in instructions
    assert "Writes are limited to" in instructions


def test_assist_session_listing_includes_started_session(client, test_project):
    body = _start_session(client, test_project.id, purpose="Listing smoke test")
    listing = client.get(f"/api/v1/projects/{test_project.id}/assist/sessions")
    assert listing.status_code == 200, listing.text
    rows = listing.json()
    matching = [r for r in rows if r["id"] == body["assist_session_id"]]
    assert len(matching) == 1
    assert matching[0]["purpose"] == "Listing smoke test"
    assert matching[0]["status"] == "active"


def test_assist_hosts_q_dsl_follow_resolves_to_operator(
    client, test_project, test_user, db_session
):
    """The headline gap-closer: ``/agent/assist/hosts?q=`` runs the full
    Hosts query DSL, and ``follow:`` resolves against the session operator —
    so an assist agent can answer "show me the hosts I have in review"
    (which the discrete filters could not express)."""
    from app.db.models import Host, HostFollow, FollowStatus

    reviewing = Host(
        project_id=test_project.id, ip_address="10.50.0.1", state="up",
        first_seen=datetime.now(timezone.utc), last_seen=datetime.now(timezone.utc),
    )
    other = Host(
        project_id=test_project.id, ip_address="10.50.0.2", state="up",
        first_seen=datetime.now(timezone.utc), last_seen=datetime.now(timezone.utc),
    )
    db_session.add_all([reviewing, other])
    db_session.commit()
    db_session.refresh(reviewing)

    # The operator (the user the client authenticates as, == session.started_by)
    # has exactly one host in review.
    db_session.add(
        HostFollow(host_id=reviewing.id, user_id=test_user.id, status=FollowStatus.IN_REVIEW)
    )
    db_session.commit()

    body = _start_session(client, test_project.id)
    headers = _auth_headers(body["api_key"])

    in_review = client.get(
        "/api/v1/agent/assist/hosts?q=follow:in_review", headers=headers
    )
    assert in_review.status_code == 200, in_review.text
    assert {h["ip_address"] for h in in_review.json()} == {"10.50.0.1"}

    # The operator has nothing marked reviewed → empty, not an error.
    reviewed = client.get(
        "/api/v1/agent/assist/hosts?q=follow:reviewed", headers=headers
    )
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json() == []


def test_assist_hosts_q_dsl_malformed_is_400(client, test_project):
    """A malformed DSL query is a clean 400 (DSLError), not a 500."""
    body = _start_session(client, test_project.id)
    headers = _auth_headers(body["api_key"])
    resp = client.get("/api/v1/agent/assist/hosts?q=port:notaport", headers=headers)
    assert resp.status_code == 400, resp.text


def test_assist_context_carries_live_prompt_version(client, test_project):
    """The /context response carries the live PROMPT_VERSION so the agent can
    verify mid-session that the deployment still matches its prompt (feedback
    #8). Mirrors the recon/execution/plan context responses."""
    from app.services.agent_prompt_history import PROMPT_VERSION

    body = _start_session(client, test_project.id)
    headers = _auth_headers(body["api_key"])
    ctx = client.get("/api/v1/agent/assist/context", headers=headers)
    assert ctx.status_code == 200, ctx.text
    assert ctx.json()["prompt_version"] == PROMPT_VERSION


def test_assist_findings_endpoint_returns_evidence(client, test_project, db_session):
    """The finding-level read (v1.45.0) — assist previously exposed only counts."""
    from app.db import models
    from app.db.models_vulnerability import (
        Vulnerability, VulnerabilitySeverity, VulnerabilitySource,
    )
    host = models.Host(ip_address="10.8.8.8", state="up", project_id=test_project.id)
    scan = models.Scan(project_id=test_project.id, filename="t.nessus", scan_type="nessus", tool_name="nessus")
    db_session.add_all([host, scan])
    db_session.commit()
    db_session.refresh(host)
    db_session.refresh(scan)
    db_session.add_all([
        Vulnerability(
            host_id=host.id, scan_id=scan.id, title="Critical RCE", severity=VulnerabilitySeverity.CRITICAL,
            source=VulnerabilitySource.NESSUS, cve_id="CVE-2024-1", cvss_score=9.8,
            exploitable=True, solution="Patch it", plugin_output="evidence here",
        ),
        Vulnerability(
            host_id=host.id, scan_id=scan.id, title="Low info", severity=VulnerabilitySeverity.LOW,
            source=VulnerabilitySource.NESSUS,
        ),
    ])
    db_session.commit()

    body = _start_session(client, test_project.id)
    resp = client.get(
        f"/api/v1/agent/assist/hosts/{host.id}/findings",
        headers=_auth_headers(body["api_key"]),
    )
    assert resp.status_code == 200, resp.text
    d = resp.json()
    assert d["total"] == 2 and d["has_more"] is False
    f0 = d["findings"][0]
    assert f0["severity"] == "critical"     # worst-first ordering
    assert f0["cve_id"] == "CVE-2024-1"
    assert f0["exploitable"] is True
    assert f0["solution"] == "Patch it"
    assert f0["evidence"] == "evidence here"


def test_assist_findings_severity_filter_and_pagination(client, test_project, db_session):
    from app.db import models
    from app.db.models_vulnerability import (
        Vulnerability, VulnerabilitySeverity, VulnerabilitySource,
    )
    host = models.Host(ip_address="10.8.8.9", state="up", project_id=test_project.id)
    scan = models.Scan(project_id=test_project.id, filename="t2.nessus", scan_type="nessus", tool_name="nessus")
    db_session.add_all([host, scan])
    db_session.commit()
    db_session.refresh(host)
    db_session.refresh(scan)
    db_session.add_all([
        Vulnerability(host_id=host.id, scan_id=scan.id, title=f"c{i}", severity=VulnerabilitySeverity.CRITICAL,
                      source=VulnerabilitySource.NESSUS)
        for i in range(3)
    ] + [
        Vulnerability(host_id=host.id, scan_id=scan.id, title="lo", severity=VulnerabilitySeverity.LOW,
                      source=VulnerabilitySource.NESSUS),
    ])
    db_session.commit()

    body = _start_session(client, test_project.id)
    headers = _auth_headers(body["api_key"])
    # severity filter
    crit = client.get(
        f"/api/v1/agent/assist/hosts/{host.id}/findings?severity=critical", headers=headers
    ).json()
    assert crit["total"] == 3
    # pagination
    page = client.get(
        f"/api/v1/agent/assist/hosts/{host.id}/findings?limit=2", headers=headers
    ).json()
    assert len(page["findings"]) == 2 and page["total"] == 4 and page["has_more"] is True


def test_assist_findings_404_for_host_outside_project(client, test_project):
    body = _start_session(client, test_project.id)
    resp = client.get(
        "/api/v1/agent/assist/hosts/999999/findings",
        headers=_auth_headers(body["api_key"]),
    )
    assert resp.status_code == 404


def test_assist_report_context_streams_full_dossier(client, test_project, db_session):
    """The scale-safe report data source: streams the complete per-host dossier
    (identity, ports, findings, notes, …) one JSON object per line, uncapped."""
    from app.db import models
    from app.db.models_vulnerability import (
        Vulnerability, VulnerabilitySeverity, VulnerabilitySource,
    )
    host = models.Host(ip_address="10.6.6.6", state="up", project_id=test_project.id)
    scan = models.Scan(project_id=test_project.id, filename="r.nessus", scan_type="nessus", tool_name="nessus")
    db_session.add_all([host, scan])
    db_session.commit()
    db_session.refresh(host)
    db_session.refresh(scan)
    db_session.add(Vulnerability(
        host_id=host.id, scan_id=scan.id, title="Report vuln",
        severity=VulnerabilitySeverity.HIGH, source=VulnerabilitySource.NESSUS, cve_id="CVE-2024-9",
    ))
    db_session.commit()

    body = _start_session(client, test_project.id)
    resp = client.get(
        "/api/v1/agent/assist/report-context.ndjson",
        headers=_auth_headers(body["api_key"]),
    )
    assert resp.status_code == 200, resp.text
    assert "ndjson" in resp.headers["content-type"]
    lines = [json.loads(l) for l in resp.text.strip().split("\n") if l]
    rec = next(r for r in lines if r["host_id"] == host.id)
    assert rec["identity"]["ip_address"] == "10.6.6.6"
    assert isinstance(rec["ports"], list)
    assert len(rec["vulnerabilities"]) >= 1        # the finding is in the dossier
    assert "dossier_summary" in rec        # full report dossier, not just counts


# ---------------------------------------------------------------------------
# Counting questions (v2.291.0)
#
# "How many hosts have critical findings and no assignee?" is a work-allocation
# question an operator actually asks, and it was the shape assist answered
# worst: /assist/hosts returns a bare list with no total, so the count could
# only come from paging to exhaustion — and an agent that stops at page one
# reports a confident wrong number. `assigned:none` didn't exist either, though
# the sibling `follow:none` did.
# ---------------------------------------------------------------------------

def test_counting_hosts_nobody_owns(client, db_session, test_project, test_user):
    """The user's question, end to end: critical findings, no assignee."""
    from datetime import datetime, timezone

    from app.db import models
    from app.db.models import Host, HostFollow, FollowStatus
    from app.db.models_vulnerability import (
        Vulnerability,
        VulnerabilitySeverity,
        VulnerabilitySource,
    )

    scan = models.Scan(project_id=test_project.id, filename="assist-count.xml", tool_name="nmap")
    db_session.add(scan)
    db_session.commit()
    db_session.refresh(scan)

    def _host(ip, *, critical: bool, assigned: bool):
        h = Host(project_id=test_project.id, ip_address=ip, state="up")
        db_session.add(h)
        db_session.commit()
        db_session.refresh(h)
        if critical:
            db_session.add(
                Vulnerability(
                    host_id=h.id, scan_id=scan.id, title=f"crit on {ip}",
                    severity=VulnerabilitySeverity.CRITICAL,
                    source=VulnerabilitySource.NESSUS, plugin_id=f"p-{ip}",
                )
            )
        if assigned:
            db_session.add(
                HostFollow(
                    host_id=h.id, user_id=test_user.id,
                    status=FollowStatus.IN_REVIEW,
                    assigned_at=datetime.now(timezone.utc),
                    assigned_by_id=test_user.id,
                )
            )
        db_session.commit()
        return h

    _host("10.9.0.1", critical=True, assigned=False)   # the answer
    _host("10.9.0.2", critical=True, assigned=False)   # the answer
    _host("10.9.0.3", critical=True, assigned=True)    # owned — excluded
    _host("10.9.0.4", critical=False, assigned=False)  # nothing critical

    started = client.post(
        f"/api/v1/projects/{test_project.id}/assist/start", json={}
    ).json()
    headers = {"X-API-Key": started["api_key"]}

    resp = client.get(
        "/api/v1/agent/assist/hosts/count",
        params={"q": "has:critical AND assigned:none"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["count"] == 2, body
    # The query is echoed so the agent can quote what it actually asked.
    assert body["query"] == "has:critical AND assigned:none"

    # The count agrees with the list it summarises — two answers to the same
    # question disagreeing is what a separately-built query would eventually do.
    listed = client.get(
        "/api/v1/agent/assist/hosts",
        params={"q": "has:critical AND assigned:none"},
        headers=headers,
    ).json()
    assert len(listed) == body["count"]
    assert {h["ip_address"] for h in listed} == {"10.9.0.1", "10.9.0.2"}


def test_assigned_none_is_the_complement_of_assigned_any(
    client, db_session, test_project, test_user
):
    """`none` has to mean exactly "not any" — if the two overlap or leave a gap,
    an operator dividing work by assignment silently loses hosts."""
    from datetime import datetime, timezone

    from app.db.models import Host, HostFollow, FollowStatus

    for ip, assigned in (("10.9.1.1", True), ("10.9.1.2", False), ("10.9.1.3", False)):
        h = Host(project_id=test_project.id, ip_address=ip, state="up")
        db_session.add(h)
        db_session.commit()
        db_session.refresh(h)
        if assigned:
            db_session.add(
                HostFollow(
                    host_id=h.id, user_id=test_user.id,
                    status=FollowStatus.IN_REVIEW,
                    assigned_at=datetime.now(timezone.utc),
                    assigned_by_id=test_user.id,
                )
            )
    db_session.commit()

    started = client.post(
        f"/api/v1/projects/{test_project.id}/assist/start", json={}
    ).json()
    headers = {"X-API-Key": started["api_key"]}

    def count(q):
        return client.get(
            "/api/v1/agent/assist/hosts/count", params={"q": q}, headers=headers
        ).json()["count"]

    total = count("")
    assert count("assigned:any") + count("assigned:none") == total
    # And it agrees with the phrasing that already worked, so the two ways of
    # asking can't diverge.
    assert count("assigned:none") == count("NOT assigned:any")
