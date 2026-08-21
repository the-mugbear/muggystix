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


def test_an_assist_key_writes_exactly_what_its_operator_may(
    client, test_project, db_session
):
    """v2.309.0 — this used to assert assist was *strictly* read-only.

    That property came from the capability system: assist sessions were minted
    with no write grants unless the operator ticked a box. The system is gone,
    and a key now does what its operator may do — so an assist session started
    by an analyst can add a note, because the analyst can. A session started by
    an auditor still cannot; that direction is covered in
    ``test_agent_operator_access.py``.

    Kept rather than deleted because "what may an assist key write" is exactly
    the question that changed, and a reader hitting this file deserves the
    answer rather than a silent absence.
    """
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
        json={"body": "written by an analyst's agent", "status": "open"},
    )
    assert note.status_code in (200, 201), note.text

    follow = client.post(
        f"/api/v1/agent/hosts/{host.id}/follow",
        headers=headers,
        json={"status": "watching"},
    )
    assert follow.status_code in (200, 204), follow.text


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


def test_assist_session_names_the_operator_it_acts_for(client, test_project):
    """v2.309.0 — was ``..._exposes_capabilities_and_operator``.

    The capability list is gone; the operator is what remains, and it is now
    the complete answer to "what may I do here" rather than half of it. An
    agent that wants to know its authority looks at who it is acting as.
    """
    body = _start_session(client, test_project.id)
    session = client.get(
        "/api/v1/agent/assist/session", headers=_auth_headers(body["api_key"])
    )
    assert session.status_code == 200, session.text
    payload = session.json()
    assert payload["operator"] is not None
    assert payload["operator"]["id"] is not None
    # The removed fields must not linger as empty shells — a client reading
    # `capabilities == []` would conclude "read-only", which is now wrong.
    assert "capabilities" not in payload
    assert "capability_constraint" not in payload


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


def test_assist_prompt_states_authority_as_the_operator(client, test_project):
    """v2.309.0 — the prompt no longer branches on a capability grant.

    It used to render one of two authority blocks, and the risk this test was
    written for was them contradicting each other. There is now a single block
    that states the rule — you act as the operator — which cannot contradict
    itself and cannot promise the agent something the server will refuse.
    """
    started = client.post(
        f"/api/v1/projects/{test_project.id}/assist/start",
        json={"purpose": "authority wording"},
    )
    assert started.status_code == 201, started.text
    instructions = started.json()["instructions"]

    assert "You act as" in instructions
    # The old read-only framing must not survive: it would tell an analyst's
    # agent it cannot write, which is now false.
    assert "read-only** assist session" not in instructions
    # And the guardrails that are still absolute have to stay absolute.
    assert "do **not** create test plans" in instructions


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


# ---------------------------------------------------------------------------
# Analyst questions the surface could not answer (v2.292.0)
#
# Assist is meant to be the place an analyst asks anything about a project. It
# was equipped for "which hosts match X" and little else: findings only per
# host, notes writable but not readable, and no way to learn the tag/site/user
# values its own query DSL accepts.
# ---------------------------------------------------------------------------

def _assist(client, project_id):
    body = client.post(f"/api/v1/projects/{project_id}/assist/start", json={}).json()
    return {"X-API-Key": body["api_key"]}


def test_findings_are_answerable_across_the_project_not_host_by_host(
    client, db_session, test_project
):
    """A finding spans hosts by design (one finding, many hosts). Reassembling
    the spine from per-host calls counted the same finding once per host — so
    the answer to "how many criticals do we have?" grew with the blast radius."""
    from app.db.models import Host
    from app.db.models_findings import Finding, FindingHost

    hosts = []
    for ip in ("10.8.0.1", "10.8.0.2", "10.8.0.3"):
        h = Host(project_id=test_project.id, ip_address=ip, state="up")
        db_session.add(h)
        db_session.commit()
        db_session.refresh(h)
        hosts.append(h)

    spanning = Finding(
        project_id=test_project.id, title="SMB signing not required",
        severity="high", status="open", source="manual",
    )
    single = Finding(
        project_id=test_project.id, title="Anonymous FTP",
        severity="critical", status="open", source="manual",
    )
    db_session.add_all([spanning, single])
    db_session.commit()
    db_session.add_all([
        FindingHost(finding_id=spanning.id, host_id=hosts[0].id),
        FindingHost(finding_id=spanning.id, host_id=hosts[1].id),
        FindingHost(finding_id=spanning.id, host_id=hosts[2].id),
        FindingHost(finding_id=single.id, host_id=hosts[0].id),
    ])
    db_session.commit()

    headers = _assist(client, test_project.id)
    body = client.get("/api/v1/agent/assist/findings", headers=headers).json()

    # Two findings, not four: the one on three hosts is one finding.
    assert body["total"] == 2
    assert body["severity_counts"] == {"high": 1, "critical": 1}
    by_title = {f["title"]: f for f in body["findings"]}
    assert by_title["SMB signing not required"]["host_count"] == 3
    assert set(by_title["SMB signing not required"]["hosts"]) == {
        "10.8.0.1", "10.8.0.2", "10.8.0.3"
    }

    # And the severity breakdown respects a filter without needing a second call.
    crit = client.get(
        "/api/v1/agent/assist/findings", params={"severity": "critical"}, headers=headers
    ).json()
    assert crit["total"] == 1
    assert crit["findings"][0]["title"] == "Anonymous FTP"


def test_unowned_findings_are_a_first_class_question(client, db_session, test_project):
    """"What has nobody picked up?" is the work-allocation question, and it is
    asked of findings as often as of hosts."""
    from app.db.models_findings import Finding

    db_session.add_all([
        Finding(project_id=test_project.id, title="Owned", severity="high",
                status="open", source="manual", owner_id=None),
        Finding(project_id=test_project.id, title="Also unowned", severity="low",
                status="open", source="manual", owner_id=None),
    ])
    db_session.commit()

    headers = _assist(client, test_project.id)
    body = client.get(
        "/api/v1/agent/assist/findings", params={"unowned": "true"}, headers=headers
    ).json()
    assert body["total"] == 2
    assert all(f["owner_username"] is None for f in body["findings"])


def test_the_agent_can_read_notes_it_could_already_write(
    client, db_session, test_project, test_user
):
    """The asymmetry that made "what do we already know about this host?"
    unanswerable — and let an agent write a note duplicating one a colleague
    added an hour earlier."""
    from app.db.models import Annotation, Host, NoteStatus

    host = Host(project_id=test_project.id, ip_address="10.8.1.1", state="up")
    db_session.add(host)
    db_session.commit()
    db_session.refresh(host)
    db_session.add(
        Annotation(
            host_id=host.id, project_id=test_project.id, user_id=test_user.id,
            body="Confirmed false positive — the banner is a honeypot.",
            status=NoteStatus.OPEN, actor_type="user",
        )
    )
    db_session.commit()

    headers = _assist(client, test_project.id)
    notes = client.get(
        f"/api/v1/agent/assist/hosts/{host.id}/notes", headers=headers
    ).json()
    assert len(notes) == 1
    assert "honeypot" in notes[0]["body"]
    # Who said it, and whether a human or an agent did — a reader needs both.
    assert notes[0]["author"] == test_user.username
    assert notes[0]["actor_type"] == "user"

    # Another project's host is not readable through this session.
    assert client.get(
        "/api/v1/agent/assist/hosts/999999/notes", headers=headers
    ).status_code == 404


def test_the_agent_can_learn_this_projects_vocabulary(
    client, db_session, test_project, test_user
):
    """A guessed tag doesn't error — it returns zero hosts, and "nothing is
    tagged production" is a confidently wrong answer to "what are the tags
    called here?"."""
    from app.db.models import HostTag

    from datetime import datetime, timezone

    from app.db.models import FollowStatus, Host, HostFollow

    db_session.add_all([
        HostTag(project_id=test_project.id, name="production"),
        HostTag(project_id=test_project.id, name="dmz"),
    ])
    # Someone holding work here — a global admin needs no membership row to be
    # assigned a host, and they are exactly who an analyst asks about.
    host = Host(project_id=test_project.id, ip_address="10.8.2.1", state="up")
    db_session.add(host)
    db_session.commit()
    db_session.refresh(host)
    db_session.add(
        HostFollow(
            host_id=host.id, user_id=test_user.id, status=FollowStatus.IN_REVIEW,
            assigned_at=datetime.now(timezone.utc), assigned_by_id=test_user.id,
        )
    )
    db_session.commit()

    headers = _assist(client, test_project.id)
    vocab = client.get("/api/v1/agent/assist/vocabulary", headers=headers).json()

    assert {"production", "dmz"} <= set(vocab["tags"])
    # The people an `assigned:<username>` query can name.
    assert test_user.username in vocab["usernames"]
    # And the fixed vocabularies, so the agent doesn't invent a status either.
    assert "false_positive" in vocab["finding_statuses"]
    assert "critical" in vocab["severities"]


def test_coverage_is_reachable_so_completeness_can_be_qualified(
    client, test_project
):
    """Every other tool reports what was found. This is the one that stops "no
    critical findings" being reported as "no critical exposure"."""
    headers = _assist(client, test_project.id)
    body = client.get("/api/v1/agent/assist/coverage", headers=headers).json()
    assert "domains" in body and "total_hosts" in body


def test_testing_history_distinguishes_a_scanner_claim_from_a_confirmed_one(
    client, db_session, test_project, test_user
):
    """v2.293.0. Assist could see what scanners reported and nothing about what
    the team did, so it could not tell a finding nobody has looked at from one a
    tester confirmed by hand — and every answer implicitly claimed the former."""
    from app.db.models import Host
    from app.db.models_agent import (
        Agent as AgentModel,
        ExecutionSession,
        TestExecutionResult,
        TestPlan,
        TestPlanEntry,
    )

    host = Host(project_id=test_project.id, ip_address="10.8.3.1", state="up")
    agent_row = AgentModel(project_id=test_project.id, name="a", owner_id=test_user.id)
    db_session.add_all([host, agent_row])
    db_session.commit()
    db_session.refresh(host)
    db_session.refresh(agent_row)

    plan = TestPlan(
        project_id=test_project.id, title="Approved plan", status="approved",
        agent_id=agent_row.id, created_by_user_id=test_user.id,
    )
    hidden = TestPlan(
        # version 2: (project_id, version) is unique, and a second plan in the
        # same project is exactly the case that constraint governs.
        project_id=test_project.id, title="Still a draft", status="draft",
        version=2, agent_id=agent_row.id, created_by_user_id=test_user.id,
    )
    # A third plan: (test_plan_id, host_id) is unique, so the rejected entry
    # cannot share a plan with the completed one — it needs its own, and it is
    # approved, so exclusion has to come from the ENTRY status rather than the
    # plan's.
    rejected_plan = TestPlan(
        project_id=test_project.id, title="Approved, entry rejected",
        status="approved", version=3, agent_id=agent_row.id,
        created_by_user_id=test_user.id,
    )
    db_session.add_all([plan, hidden, rejected_plan])
    db_session.commit()

    entry = TestPlanEntry(
        test_plan_id=plan.id, host_id=host.id, priority="high",
        test_phase="enumeration", status="completed",
        rationale="FTP banner suggests anonymous login",
        proposed_tests=[{"tool": "nmap", "description": "confirm ftp"}],
    )
    rejected = TestPlanEntry(
        test_plan_id=rejected_plan.id, host_id=host.id, priority="low",
        test_phase="enumeration", status="rejected",
        rationale="decided against", proposed_tests=[],
    )
    draft_entry = TestPlanEntry(
        test_plan_id=hidden.id, host_id=host.id, priority="low",
        test_phase="enumeration", status="proposed",
        rationale="not approved yet", proposed_tests=[],
    )
    db_session.add_all([entry, rejected, draft_entry])
    db_session.commit()

    exec_session = ExecutionSession(
        test_plan_id=plan.id, agent_id=agent_row.id, started_by_id=test_user.id,
        status="completed",
    )
    db_session.add(exec_session)
    db_session.commit()
    db_session.add(
        TestExecutionResult(
            execution_session_id=exec_session.id, entry_id=entry.id, test_index=0,
            status="executed", command_run="nmap -p21 -sV 10.8.3.1",
            findings_summary="Anonymous login accepted", severity="critical",
            is_finding=True,
        )
    )
    db_session.commit()

    headers = _assist(client, test_project.id)
    body = client.get(
        f"/api/v1/agent/assist/hosts/{host.id}/testing", headers=headers
    ).json()

    # The approved, non-rejected entry only: a draft plan's entries never leak,
    # and a rejected entry is an explicit "do not test this" that an agent must
    # not re-litigate as outstanding work.
    assert len(body) == 1
    e = body[0]
    assert e["status"] == "completed" and e["plan_title"] == "Approved plan"
    assert e["proposed_tests"][0]["tool"] == "nmap"
    # The part that makes a claim citable: what was actually run, and what it showed.
    assert e["results"][0]["command_run"] == "nmap -p21 -sV 10.8.3.1"
    assert e["results"][0]["is_finding"] is True
    assert e["results"][0]["severity"] == "critical"


def test_segments_rank_the_network_so_the_agent_does_not_have_to(
    client, db_session, test_project
):
    """"Which segment is worst?" was a count per subnet reassembled client-side
    — arithmetic an agent does silently and sometimes wrongly. The ordering is
    the answer, so the server does it.

    v2.297.0 — the seed changed from raw ``Vulnerability`` rows to **active
    Findings**, because the endpoint now wraps ``compute_subnet_insights`` and
    that is what every other surface counts. An untriaged scanner row is not
    yet a finding, and the agent should not rank segments by one.
    """
    from app.db.models import Host, Scope, Subnet, HostSubnetMapping
    from app.db.models_findings import Finding, FindingHost

    scope = Scope(name="s", project_id=test_project.id)
    db_session.add(scope)
    db_session.commit()
    quiet = Subnet(scope_id=scope.id, cidr="10.20.0.0/24", description="quiet")
    noisy = Subnet(scope_id=scope.id, cidr="10.21.0.0/24", description="noisy")
    db_session.add_all([quiet, noisy])
    db_session.commit()

    for subnet, ip, critical in (
        (quiet, "10.20.0.5", False), (noisy, "10.21.0.5", True), (noisy, "10.21.0.6", True),
    ):
        h = Host(project_id=test_project.id, ip_address=ip, state="up")
        db_session.add(h)
        db_session.commit()
        db_session.refresh(h)
        db_session.add(HostSubnetMapping(host_id=h.id, subnet_id=subnet.id))
        if critical:
            f = Finding(
                project_id=test_project.id, title=f"c-{ip}", severity="critical",
                status="open", source="manual",
            )
            db_session.add(f)
            db_session.commit()
            db_session.add(FindingHost(finding_id=f.id, host_id=h.id))
    db_session.commit()

    headers = _assist(client, test_project.id)
    body = client.get("/api/v1/agent/assist/segments", headers=headers).json()
    segments = body["subnets"]
    by_cidr = {s["cidr"]: s for s in segments}

    assert by_cidr["10.21.0.0/24"]["exposure"]["by_severity"]["critical"] == 2
    assert by_cidr["10.20.0.0/24"]["exposure"]["by_severity"]["critical"] == 0
    # Worst first — the ordering IS the answer to "where should we look?".
    assert segments[0]["cidr"] == "10.21.0.0/24"
    # Nobody owns any of them yet, which is the other half of the question —
    # now under `neglect`, alongside unreviewed hosts and staleness.
    assert by_cidr["10.21.0.0/24"]["neglect"]["unowned_active_findings"] == 2
    assert by_cidr["10.21.0.0/24"]["neglect"]["unreviewed_hosts"] == 2


def test_recent_notes_answer_what_the_team_has_been_doing(
    client, db_session, test_project, test_user
):
    """Per-host notes answer "what about THIS host"; picking an engagement back
    up is a question about the work, not one asset."""
    from app.db.models import Annotation, Host, NoteStatus

    host = Host(project_id=test_project.id, ip_address="10.8.4.1", state="up")
    db_session.add(host)
    db_session.commit()
    db_session.refresh(host)
    db_session.add_all([
        Annotation(host_id=host.id, project_id=test_project.id, user_id=test_user.id,
                   body="Still chasing the vendor", status=NoteStatus.OPEN, actor_type="user"),
        Annotation(host_id=host.id, project_id=test_project.id, user_id=test_user.id,
                   body="Closed this one out", status=NoteStatus.RESOLVED, actor_type="user"),
    ])
    db_session.commit()

    headers = _assist(client, test_project.id)
    all_notes = client.get("/api/v1/agent/assist/notes", headers=headers).json()
    assert len(all_notes) == 2
    # The host is resolved so the agent can say which asset without another call.
    assert all_notes[0]["host_ip"] == "10.8.4.1"

    # Open notes are the outstanding-work list the project actually keeps.
    open_only = client.get(
        "/api/v1/agent/assist/notes", params={"status": "open"}, headers=headers
    ).json()
    assert [n["body"] for n in open_only] == ["Still chasing the vendor"]


# ---------------------------------------------------------------------------
# v2.294.0 — posture, patterns, and the evidence behind a finding
# ---------------------------------------------------------------------------

def test_posture_gives_the_agent_the_same_headline_the_ui_shows(
    client, db_session, test_project
):
    """"Where is this project?" must resolve to ONE answer.

    The agent and the Posture page have to quote the same condition — an
    agent that recomputed exposure from raw counts would disagree with the
    page a manager is reading, and the disagreement would surface as the
    agent being wrong. So this asserts the endpoint returns what
    compute_posture computed, not a second derivation of it.
    """
    from app.services.posture_service import compute_posture

    headers = _assist(client, test_project.id)
    resp = client.get("/api/v1/agent/assist/posture", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    truth = compute_posture(db_session, test_project.id, use_cache=False)
    assert body["label"] == truth["label"]
    assert body["headline"]["active_exposure"] == truth["headline"]["active_exposure"]
    assert body["evidence"]["scan_count"] == truth["evidence"]["scan_count"]

    # The trims are deliberate, not accidental: the UI heatmap is a picture,
    # and the full systemic block belongs to /assist/patterns.
    assert "heatmap" not in body
    assert "systemic" not in body
    assert "systemic" in body["headline"]  # the counts survive


def test_posture_never_reports_an_unassessed_estate_as_clean(client, test_project):
    """A project with nothing in it must not read 'no_urgent_signals'.

    Absence of findings is absence of assessment here, and an agent that
    reports it as a clean bill of health is stating the most damaging wrong
    thing it could say about an engagement. Which non-clean label it lands on
    ('needs_assessment' when a signal fires, 'insufficient_evidence' when the
    evidence gate catches it) is the posture model's business — this pins only
    that the reassuring one is unreachable without evidence.
    """
    headers = _assist(client, test_project.id)
    body = client.get("/api/v1/agent/assist/posture", headers=headers).json()
    assert body["label"] != "no_urgent_signals"
    assert body["conclusion"]


def test_patterns_says_not_assessable_rather_than_nothing_found(
    client, test_project
):
    """Without scoped subnets the cross-sectional analysis cannot run at all.
    That has to be distinguishable from 'it ran and found nothing' — the two
    read identically to an agent unless the payload says so."""
    headers = _assist(client, test_project.id)
    resp = client.get("/api/v1/agent/assist/patterns", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["adopted"] is False
    assert "scope" in body["reason"].lower()


def test_patterns_surfaces_the_subnet_comparison_posture_does_not(
    client, db_session, test_project
):
    """The 'this subnet looks worse than the others' claim lives only here —
    compute_posture carries conditions and blind spots but has never carried
    segment_outliers, which is exactly the comparison an analyst asks for."""
    from app.db.models import Scope, Subnet

    scope = Scope(project_id=test_project.id, name="Corp")
    db_session.add(scope)
    db_session.commit()
    db_session.add(Subnet(scope_id=scope.id, cidr="10.9.0.0/24"))
    db_session.commit()

    headers = _assist(client, test_project.id)
    body = client.get("/api/v1/agent/assist/patterns", headers=headers).json()
    assert body["adopted"] is True
    for key in ("estate", "blind_spots", "segment_outliers", "conditions",
                "family_summary", "diagnostic_profiles"):
        assert key in body, key
    # The UI's heatmap grid is not the agent's business.
    assert "family_matrix" not in body


def test_finding_detail_reaches_the_evidence_a_writeup_cites(
    client, db_session, test_project, test_user
):
    """The report stage turns on this: a promoted finding's justification note
    and its screenshots. Listing findings gives titles and severities; without
    the evidence an agent asked to 'write up the findings' has nothing to cite
    but the title it was already given."""
    from app.db.models import Annotation, Host, NoteAttachment, NoteStatus
    from app.db.models_findings import Finding, FindingHost

    host = Host(project_id=test_project.id, ip_address="10.9.0.5", state="up")
    db_session.add(host)
    db_session.commit()
    db_session.refresh(host)

    note = Annotation(
        host_id=host.id, project_id=test_project.id, user_id=test_user.id,
        body="Confirmed anonymous bind on the LDAP service; screenshot attached.",
        status=NoteStatus.OPEN, note_type="finding",
    )
    db_session.add(note)
    db_session.commit()
    db_session.refresh(note)

    db_session.add(NoteAttachment(
        annotation_id=note.id, project_id=test_project.id,
        filename="ldap-anon-bind.png", content_type="image/png",
        size_bytes=48_213, storage_path=f"{note.id}/ldap-anon-bind.png",
        uploaded_by_id=test_user.id,
    ))
    finding = Finding(
        project_id=test_project.id, title="LDAP allows anonymous bind",
        severity="high", status="confirmed", source="note",
        evidence_annotation_id=note.id, created_by_id=test_user.id,
    )
    db_session.add(finding)
    db_session.commit()
    db_session.add(FindingHost(finding_id=finding.id, host_id=host.id))
    # The finding's own discussion thread — a second, separate note path.
    db_session.add(Annotation(
        finding_id=finding.id, user_id=test_user.id,
        body="Retest after the vendor patch lands.", status=NoteStatus.OPEN,
    ))
    db_session.commit()

    headers = _assist(client, test_project.id)
    resp = client.get(f"/api/v1/agent/assist/findings/{finding.id}", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["title"] == "LDAP allows anonymous bind"
    assert body["host_count"] == 1
    assert body["hosts"][0]["ip_address"] == "10.9.0.5"

    # The justification note, and the comment thread, are distinct things.
    assert "anonymous bind" in body["evidence_note"]["body"]
    assert [c["body"] for c in body["comments"]] == ["Retest after the vendor patch lands."]

    # Screenshots are references, not bytes: a base64 payload would cost
    # thousands of tokens for an image the model cannot show anyone, and the
    # report needs the file on disk beside it regardless.
    att = body["evidence_note"]["attachments"][0]
    assert att["filename"] == "ldap-anon-bind.png"
    assert att["size_bytes"] == 48_213
    assert att["download_path"] == f"/api/v1/agent/assist/attachments/{att['id']}"
    assert "data" not in att and "content" not in att


def test_finding_detail_is_project_scoped(client, db_session, test_project):
    """A finding in another project must be indistinguishable from one that
    does not exist — 403-vs-404 leaks that the id is real."""
    from app.db.models_project import Project
    from app.db.models_findings import Finding

    other = Project(name="Someone else's engagement", slug="other-engagement-a")
    db_session.add(other)
    db_session.commit()
    foreign = Finding(
        project_id=other.id, title="Not yours", severity="critical",
        status="open", source="manual",
    )
    db_session.add(foreign)
    db_session.commit()

    headers = _assist(client, test_project.id)
    resp = client.get(f"/api/v1/agent/assist/findings/{foreign.id}", headers=headers)
    assert resp.status_code == 404


def test_attachment_download_is_project_scoped(client, db_session, test_project, test_user):
    """The download path handed out by assist_get_finding is key-authenticated
    and project-scoped; it must not become a way to read another engagement's
    evidence by guessing an id."""
    from app.db.models import Annotation, NoteAttachment, NoteStatus
    from app.db.models_project import Project

    other = Project(name="Other engagement", slug="other-engagement-b")
    db_session.add(other)
    db_session.commit()
    note = Annotation(
        project_id=other.id, user_id=test_user.id, body="theirs",
        status=NoteStatus.OPEN,
    )
    db_session.add(note)
    db_session.commit()
    att = NoteAttachment(
        annotation_id=note.id, project_id=other.id, filename="theirs.png",
        content_type="image/png", size_bytes=10, storage_path=f"{note.id}/theirs.png",
    )
    db_session.add(att)
    db_session.commit()

    headers = _assist(client, test_project.id)
    resp = client.get(f"/api/v1/agent/assist/attachments/{att.id}", headers=headers)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# P2 — completeness of the picture (v2.297.0)
# ---------------------------------------------------------------------------

def test_segments_agree_with_the_subnet_insights_page(client, db_session, test_project):
    """The rollup must be the service's, not a second implementation of it.

    The hand-rolled version counted raw Vulnerability rows (every other surface
    counts active Findings) and read HostSubnetMapping directly, which
    double-counts a host that sits in two overlapping scoped ranges. An agent
    quoting a number no page will ever show is the failure this pins.
    """
    from app.db.models import Scope, Subnet
    from app.services.subnet_insight_service import compute_subnet_insights

    scope = Scope(project_id=test_project.id, name="segments-parity")
    db_session.add(scope)
    db_session.commit()
    # Deliberately overlapping: a /16 and a /24 inside it. This is the shape
    # that made the old implementation count the same host twice.
    for cidr in ("10.20.0.0/16", "10.20.5.0/24"):
        db_session.add(Subnet(scope_id=scope.id, cidr=cidr))
    db_session.commit()

    headers = _assist(client, test_project.id)
    resp = client.get("/api/v1/agent/assist/segments", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["adopted"] is True

    expected = compute_subnet_insights(db_session, test_project.id, limit=25, offset=0)
    assert body["total"] == expected["total"]
    assert body["totals"]["hosts_in_scope"] == expected["totals"]["hosts_in_scope"]
    assert [s["cidr"] for s in body["subnets"]] == [s["cidr"] for s in expected["subnets"]]
    # The blocks the P2 item existed for.
    first = body["subnets"][0]
    for block in ("exposure", "neglect", "hygiene", "recommended_action"):
        assert block in first, f"{block} missing from the segment payload"
    # eol_os_detail is the per-host list; "which hosts are EOL here" is a q=
    # filter, and carrying it per subnet per page is context spent on data the
    # agent can ask for.
    assert "eol_os_detail" not in first["hygiene"]
    assert "eol_os_hosts" in first["hygiene"]


def test_segments_report_not_assessable_rather_than_clean(client, test_project):
    """No scoped subnets means the analysis cannot run. Returning an empty list
    would read as "no problem segments", which is the reassuring wrong answer."""
    headers = _assist(client, test_project.id)
    body = client.get("/api/v1/agent/assist/segments", headers=headers).json()
    assert body["adopted"] is False
    assert body["subnets"] == []
    assert "scope" in (body["reason"] or "").lower()


def test_ingestion_issues_separates_absent_from_never_parsed(
    client, db_session, test_project
):
    """The point of the tool: "no data" and "the upload failed" must not look
    the same. A failed job and its ParseError are ONE upload — reporting both
    would inflate the count and read as two broken files."""
    from app.db import models

    err = models.ParseError(
        project_id=test_project.id, filename="httpx-out.json",
        file_type="httpx_json", error_type="parsing_error",
        error_message="Expecting value: line 1 column 1",
        user_message="This file isn't valid JSON — check the httpx -json flag.",
        status="unresolved",
    )
    db_session.add(err)
    db_session.commit()
    db_session.add(models.IngestionJob(
        project_id=test_project.id, filename="httpx-out.json",
        original_filename="httpx-out.json", storage_path="/tmp/httpx-out.json",
        status="failed", tool_name="httpx", parse_error_id=err.id,
        error_message="parser raised",
    ))
    db_session.commit()

    headers = _assist(client, test_project.id)
    body = client.get("/api/v1/agent/assist/ingestion-issues", headers=headers).json()

    assert body["has_issues"] is True
    assert body["failed"] == 1
    # The ParseError is claimed by the job, so it is NOT listed again.
    assert body["unresolved_parse_errors"] == 0
    assert len(body["issues"]) == 1
    issue = body["issues"][0]
    assert issue["kind"] == "failed"
    # The job's own "parser raised" is useless to an operator; the parse
    # error's user-facing message is what gets folded in.
    assert "valid JSON" in issue["message"]


def test_ingestion_issues_surfaces_a_completed_but_degraded_upload(
    client, db_session, test_project
):
    """The quiet case. The job says completed and every other surface agrees,
    but rows were dropped — so counts drawn from it are undercounts."""
    from app.db import models

    db_session.add(models.IngestionJob(
        project_id=test_project.id, filename="eyewitness.zip",
        original_filename="eyewitness.zip", storage_path="/tmp/eyewitness.zip",
        status="completed", tool_name="eyewitness", skipped_count=30,
        parser_warnings="30 rows missing a url column",
    ))
    db_session.commit()

    headers = _assist(client, test_project.id)
    body = client.get("/api/v1/agent/assist/ingestion-issues", headers=headers).json()
    assert body["degraded"] == 1
    assert body["has_issues"] is True
    issue = next(i for i in body["issues"] if i["kind"] == "degraded")
    assert issue["skipped_count"] == 30
    assert "incomplete" in issue["message"]


def test_ingestion_issues_is_quiet_when_nothing_is_wrong(client, test_project):
    """has_issues=false is what lets the agent report an absence as real."""
    headers = _assist(client, test_project.id)
    body = client.get("/api/v1/agent/assist/ingestion-issues", headers=headers).json()
    assert body["has_issues"] is False
    assert body["issues"] == []


def test_host_detail_carries_web_interfaces_and_screenshot_references(
    client, db_session, test_project
):
    """A write-up could say a host serves HTTP but nothing about what it
    serves. Screenshots follow the attachment contract: a path, never bytes."""
    from app.db import models

    host = models.Host(project_id=test_project.id, ip_address="10.30.0.9", state="up")
    db_session.add(host)
    db_session.flush()
    scan = models.Scan(project_id=test_project.id, filename="ew.zip", tool_name="eyewitness")
    db_session.add(scan)
    db_session.flush()
    db_session.add(models.WebInterface(
        host_id=host.id, scan_id=scan.id, project_id=test_project.id,
        source="eyewitness", url="https://10.30.0.9/admin", port=443,
        status_code=200, title="Router admin", server_header="lighttpd/1.4",
        technologies=["lighttpd 1.4", "jQuery"],
        screenshot_path=f"{scan.id}/admin.png",
    ))
    db_session.add(models.WebInterface(
        host_id=host.id, scan_id=scan.id, project_id=test_project.id,
        source="httpx", url="http://10.30.0.9/", port=80, status_code=301,
    ))
    db_session.commit()

    headers = _assist(client, test_project.id)
    body = client.get(f"/api/v1/agent/assist/hosts/{host.id}", headers=headers).json()

    interfaces = body["web_interfaces"]
    assert len(interfaces) == 2
    # Screenshotted first — those are the ones a report can show.
    shot = interfaces[0]
    assert shot["title"] == "Router admin"
    assert shot["technologies"] == ["lighttpd 1.4", "jQuery"]
    assert shot["screenshot_download_path"] == (
        f"/api/v1/agent/assist/web-interfaces/{shot['id']}/screenshot"
    )
    # httpx records the interface without a capture — null, not a dead path.
    assert interfaces[1]["screenshot_download_path"] is None


def test_web_screenshot_download_is_project_scoped(client, db_session, test_project):
    """Same boundary as the attachment download: another engagement's capture
    must be indistinguishable from one that doesn't exist."""
    from app.db import models
    from app.db.models_project import Project

    other = Project(name="Other engagement", slug="other-engagement-c")
    db_session.add(other)
    db_session.commit()
    scan = models.Scan(project_id=other.id, filename="theirs.zip", tool_name="eyewitness")
    db_session.add(scan)
    db_session.flush()
    iface = models.WebInterface(
        scan_id=scan.id, project_id=other.id, source="eyewitness",
        url="https://10.99.0.1/", screenshot_path=f"{scan.id}/theirs.png",
    )
    db_session.add(iface)
    db_session.commit()

    headers = _assist(client, test_project.id)
    resp = client.get(
        f"/api/v1/agent/assist/web-interfaces/{iface.id}/screenshot", headers=headers,
    )
    assert resp.status_code == 404


def test_the_retired_write_flag_is_refused_not_ignored(client, db_session, test_project):
    """v2.310.0 — a caller asking for authority that no longer exists gets an
    error, not a silent upgrade.

    ``can_write_assigned`` was removed with the capability system. Pydantic's
    default is ``extra="ignore"``, which would have produced the worst possible
    shape: a script sending ``false`` to request a READ-ONLY key gets a 201 and
    a key carrying its operator's full write authority. Sending ``true`` is
    wrong in the other direction — it asked for "assigned hosts only" and would
    get the whole project.

    Both values are refused, and nothing is persisted, so a caller cannot end
    up holding a credential it did not ask for.
    """
    from app.db.models_agent import AssistSession

    before = db_session.query(AssistSession).count()
    for value in (False, True):
        resp = client.post(
            f"/api/v1/projects/{test_project.id}/assist/start",
            json={"purpose": "legacy client", "can_write_assigned": value},
        )
        assert resp.status_code == 422, (
            f"can_write_assigned={value} was accepted: {resp.text}. Ignoring it "
            "silently grants authority the caller did not request."
        )
        assert "can_write_assigned" in resp.text

    db_session.expire_all()
    assert db_session.query(AssistSession).count() == before, (
        "a refused start still created a session — the caller would be holding "
        "a key it was told it could not have"
    )


def test_omitting_the_retired_flag_still_works(client, test_project):
    """The rejection must not catch the normal path: `None` means 'not sent'."""
    resp = client.post(
        f"/api/v1/projects/{test_project.id}/assist/start",
        json={"purpose": "current client"},
    )
    assert resp.status_code == 201, resp.text
