"""Security Posture composition — the parts most likely to regress: the
deterministic label, the headline measures, and that the label/reasons/
priorities share one signal pass (so they can't disagree).
"""
from __future__ import annotations

from app.db import models
from app.db.models_findings import Finding, FindingHost
from app.services.posture_service import compute_posture


def _host(db, project_id, ip):
    h = models.Host(project_id=project_id, ip_address=ip, state="up")
    db.add(h)
    db.flush()
    return h


def _finding(db, project_id, *, severity, status="open", owner_id=None, host=None):
    f = Finding(project_id=project_id, title=f"{severity} finding", severity=severity,
                status=status, source="manual", owner_id=owner_id)
    db.add(f)
    db.flush()
    if host is not None:
        db.add(FindingHost(finding_id=f.id, host_id=host.id, host_status="open"))
        db.flush()
    return f


def test_empty_project_reads_needs_assessment(db_session, test_project):
    """Absence of findings is NOT health — a never-scanned estate must not read
    as 'no urgent signals'."""
    out = compute_posture(db_session, test_project.id, use_cache=False)
    assert out["label"] == "needs_assessment"
    assert any(p["kind"] == "onboard" for p in out["priorities"])
    assert out["headline"]["active_exposure"]["active_findings"] == 0
    # Evidence currency is always present (even with no scans).
    assert "scan_staleness_days" in out["evidence"]
    # Systemic carries an adoption flag so the UI can distinguish
    # "can't assess" from "assessed, nothing found".
    assert "adopted" in out["headline"]["systemic"]


def test_active_critical_is_action_required_whoever_holds_it(db_session, test_project, test_user):
    """v2.373.0 — the label follows SEVERITY.  It used to fire only for an
    UNOWNED critical/high, so assigning an analyst to a confirmed critical
    turned the label to "no urgent signals": assignment is assessment work and
    says nothing about what was observed."""
    host = _host(db_session, test_project.id, "10.0.0.10")
    db_session.add(models.HostFollow(
        host_id=host.id, user_id=test_user.id, status=models.FollowStatus.REVIEWED.value,
    ))
    db_session.add(models.Scan(project_id=test_project.id, filename="s", tool_name="nmap", scan_type="nmap"))
    db_session.add(models.Port(host_id=host.id, port_number=443, protocol="tcp",
                               state="open", service_name="https"))
    f = _finding(db_session, test_project.id, severity="critical", owner_id=None, host=host)
    db_session.commit()

    out = compute_posture(db_session, test_project.id, use_cache=False)
    assert out["label"] == "action_required"
    assert out["headline"]["active_exposure"]["by_severity"]["critical"] == 1
    assert out["priorities"][0]["kind"] == "exposure"
    # Unassigned is listed as work — and explains nothing about the label.
    work = [p for p in out["priorities"] if p["kind"] == "ownership"]
    assert len(work) == 1 and work[0]["tier"] == "work"
    assert not any("assigned" in r["text"].lower() for r in out["reasons"])

    # Assign an analyst: the work row goes, the label does not move.
    f.owner_id = test_user.id
    db_session.commit()
    out = compute_posture(db_session, test_project.id, use_cache=False)
    assert out["label"] == "action_required"
    assert not [p for p in out["priorities"] if p["kind"] == "ownership"]


def test_open_questions_count_opens_exactly_its_hosts(client, db_session, test_project, test_user):
    """"Still needs evidence" is an explicit record (a review that concluded
    needs_evidence), and `conclusion:needs_evidence` is its list."""
    def reviewed(ip, conclusion, status=models.FollowStatus.REVIEWED.value):
        h = _host(db_session, test_project.id, ip)
        db_session.add(models.HostFollow(host_id=h.id, user_id=test_user.id, status=status,
                                         review_conclusion=conclusion))

    reviewed("10.9.0.1", "needs_evidence")
    reviewed("10.9.0.2", "no_issue")
    # Back in review: the old conclusion no longer stands.
    reviewed("10.9.0.3", "needs_evidence", status=models.FollowStatus.IN_REVIEW.value)
    _host(db_session, test_project.id, "10.9.0.4")
    db_session.commit()

    out = compute_posture(db_session, test_project.id, use_cache=False)
    assert out["headline"]["open_questions"]["needs_evidence_hosts"] == 1

    r = client.get(f"/api/v1/projects/{test_project.id}/hosts/", params={"q": "conclusion:needs_evidence"})
    assert r.status_code == 200, r.text
    assert [i["ip_address"] for i in r.json()["items"]] == ["10.9.0.1"]
    bad = client.get(f"/api/v1/projects/{test_project.id}/hosts/", params={"q": "conclusion:maybe"})
    assert bad.status_code == 400


def test_unassigned_low_finding_alone_does_not_move_the_label(db_session, test_project, test_user):
    host = _host(db_session, test_project.id, "10.0.0.11")
    db_session.add(models.HostFollow(
        host_id=host.id, user_id=test_user.id, status=models.FollowStatus.REVIEWED.value,
    ))
    db_session.add(models.Scan(project_id=test_project.id, filename="s", tool_name="nmap", scan_type="nmap"))
    db_session.add(models.Port(host_id=host.id, port_number=443, protocol="tcp",
                               state="open", service_name="https"))
    _finding(db_session, test_project.id, severity="low", owner_id=None, host=host)
    db_session.commit()

    out = compute_posture(db_session, test_project.id, use_cache=False)
    assert out["label"] == "no_urgent_signals"
    assert [p["tier"] for p in out["priorities"]] == ["work"]
    assert out["reasons"] == []


def test_owned_reviewed_finding_no_urgent_signals(db_session, test_project, test_user):
    """A single owned, non-critical finding on a reviewed host, with no systemic
    spread AND scan evidence present, produces no action/assess signals."""
    host = _host(db_session, test_project.id, "10.0.0.20")
    db_session.add(models.HostFollow(
        host_id=host.id, user_id=test_user.id, status=models.FollowStatus.REVIEWED.value,
    ))
    # Scan evidence exists AND the host has a characterised service — the estate
    # has been meaningfully assessed (clears the per-domain minimum gate).
    db_session.add(models.Scan(project_id=test_project.id, filename="s", tool_name="nmap", scan_type="nmap"))
    db_session.add(models.Port(host_id=host.id, port_number=443, protocol="tcp",
                               state="open", service_name="https"))
    _finding(db_session, test_project.id, severity="low", owner_id=test_user.id, host=host)
    db_session.commit()

    out = compute_posture(db_session, test_project.id, use_cache=False)
    assert out["label"] == "no_urgent_signals"
    assert out["headline"]["review_coverage"]["pct"] == 100
    assert out["headline"]["ownership"]["unowned"] == 0


def test_owned_reviewed_finding_without_scans_is_insufficient_evidence(db_session, test_project, test_user):
    """The false-green guard: the SAME owned+reviewed finding but with NO scan
    evidence must read 'insufficient_evidence', not 'no_urgent_signals'. Absence
    of evidence is never a reassuring result."""
    host = _host(db_session, test_project.id, "10.0.0.21")
    db_session.add(models.HostFollow(
        host_id=host.id, user_id=test_user.id, status=models.FollowStatus.REVIEWED.value,
    ))
    _finding(db_session, test_project.id, severity="low", owner_id=test_user.id, host=host)
    db_session.commit()

    out = compute_posture(db_session, test_project.id, use_cache=False)
    assert out["evidence"]["scan_count"] == 0
    assert out["label"] == "insufficient_evidence"


def test_blocked_run_is_not_a_strategic_signal(db_session, test_project):
    """A blocked execution session must NOT escalate the strategic label or
    appear as a management priority — it's operational state (kept in decisions),
    not a security-condition signal."""
    from app.db.models_agent import TestPlan, ExecutionSession

    host = _host(db_session, test_project.id, "10.0.0.30")
    db_session.add(models.Scan(project_id=test_project.id, filename="s", tool_name="nmap", scan_type="nmap"))
    plan = TestPlan(project_id=test_project.id, title="plan")
    db_session.add(plan)
    db_session.flush()
    db_session.add(ExecutionSession(test_plan_id=plan.id, status="failed"))
    db_session.commit()

    out = compute_posture(db_session, test_project.id, use_cache=False)
    assert out["decisions"]["blocked_sessions"] == 1          # still counted...
    assert out["label"] != "action_required"                 # ...but not a label driver
    assert not any(p["kind"] == "blocked" for p in out["priorities"])


def test_blocked_runs_count_only_latest_session_per_plan(db_session, test_project):
    """A superseded failed session (a newer run was started) must NOT leave a
    permanent 'blocked' flag — only the latest session per plan counts."""
    from app.db.models_agent import TestPlan, ExecutionSession

    plan = TestPlan(project_id=test_project.id, title="plan")
    db_session.add(plan)
    db_session.flush()
    # Older session failed; a newer session is active → the plan is progressing.
    db_session.add(ExecutionSession(test_plan_id=plan.id, status="failed"))
    db_session.flush()
    db_session.add(ExecutionSession(test_plan_id=plan.id, status="active"))
    db_session.commit()

    out = compute_posture(db_session, test_project.id, use_cache=False)
    assert out["decisions"]["blocked_sessions"] == 0
    assert not any(p["kind"] == "blocked" for p in out["priorities"])

    # Now the LATEST session fails → it counts.
    db_session.add(ExecutionSession(test_plan_id=plan.id, status="failed"))
    db_session.commit()
    out2 = compute_posture(db_session, test_project.id, use_cache=False)
    assert out2["decisions"]["blocked_sessions"] == 1


def test_posture_response_contract(db_session, test_project):
    """Pin the response shape the frontend TypeScript depends on — renames
    (confirmed_exposure→active_exposure, analyst_active→non_scanner_active) have
    drifted from the manual TS interface before; this fails loudly on the next."""
    out = compute_posture(db_session, test_project.id, use_cache=False)
    assert set(out) >= {
        "label", "conclusion", "reasons", "headline",
        "priorities", "decisions", "sites", "systemic", "disposition", "evidence",
    }
    assert set(out["conclusion"]) >= {"text", "tone"}
    # v2.372.0 — the engagement ends at the report; remediated / reopened /
    # backlog-age measure a response BlueStick does not assess.  Pinned absent
    # so the block is not reintroduced by habit.
    assert "remediation_flow" not in out
    assert set(out["headline"]) >= {
        "active_exposure", "review_coverage", "ownership", "systemic", "detected_exposure",
    }
    assert "adopted" in out["headline"]["systemic"]
    assert set(out["disposition"]) >= {"scanner_active", "non_scanner_active", "by_status"}
    assert set(out["decisions"]) >= {"pending_approvals", "blocked_sessions"}
    assert set(out["evidence"]) >= {"scan_count", "scan_staleness_days"}
    for p in out["priorities"]:
        assert set(p) >= {"kind", "title", "blast_radius", "action", "severity", "owner", "link"}


def test_heatmap_present_with_scoped_estate(db_session, test_project):
    """When scoped subnets exist, the heatmap carries family rows × site columns
    with affected/assessed cells; absent (null) when systemic isn't adopted."""
    from app.db.models import Scope, Subnet, Site, HostSubnetMapping

    # No scopes yet → systemic not adopted → heatmap is null.
    out0 = compute_posture(db_session, test_project.id, use_cache=False)
    assert out0["heatmap"] is None

    scope = Scope(project_id=test_project.id, name="scope")
    db_session.add(scope)
    site = Site(project_id=test_project.id, name="HQ", criticality_tier=1)
    db_session.add(site)
    db_session.flush()
    sn = Subnet(scope_id=scope.id, cidr="10.4.4.0/24", site="HQ", site_id=site.id)
    db_session.add(sn)
    db_session.flush()
    for i in range(1, 4):
        h = models.Host(project_id=test_project.id, ip_address=f"10.4.4.{i}", state="up",
                        os_name="Windows XP")  # EOL
        db_session.add(h)
        db_session.flush()
        db_session.add(HostSubnetMapping(host_id=h.id, subnet_id=sn.id))
        db_session.add(models.Port(host_id=h.id, port_number=445, protocol="tcp", state="open"))
    # A fourth host with a port but NO fingerprinted OS: eligible, not assessed.
    blind = models.Host(project_id=test_project.id, ip_address="10.4.4.9", state="up")
    db_session.add(blind)
    db_session.flush()
    db_session.add(HostSubnetMapping(host_id=blind.id, subnet_id=sn.id))
    db_session.add(models.Port(host_id=blind.id, port_number=22, protocol="tcp", state="open"))
    db_session.commit()

    out = compute_posture(db_session, test_project.id, use_cache=False)
    hm = out["heatmap"]
    assert hm is not None
    assert any(seg["label"] == "HQ" for seg in hm["segments"])
    lifecycle = next((r for r in hm["rows"] if r["family"] == "lifecycle_patching"), None)
    assert lifecycle is not None
    cell = lifecycle["cells"][0]
    assert cell["numerator"] == 3 and cell["denominator"] == 3   # 3/3 EOL in HQ
    assert cell["drilldown_filter"]["conditions"] == ["eol_os"]
    # v2.373.0 — evidence completeness: OS identification applies to the 4 hosts
    # with ports, 3 of which carry it; the site has 4 hosts in scope.
    assert (cell["eligible"], cell["eligible_assessed"], cell["in_scope"]) == (4, 3, 4)


def test_the_unassigned_cell_opens_exactly_the_hosts_it_counts(client, db_session, test_project):
    """v2.372.0 — the grid's "Unassigned" column carried ``site: null`` and the
    link built from it had NO site filter, so a cell counting 2 hosts opened
    every site's affected hosts.  ``site:none`` is that column as a filter:
    inside a scoped subnet, no site inherited — not merely "not in a named site"."""
    from app.db.models import Scope, Subnet, Site, HostSubnetMapping

    scope = Scope(project_id=test_project.id, name="scope")
    site = Site(project_id=test_project.id, name="HQ", criticality_tier=1)
    db_session.add_all([scope, site])
    db_session.flush()
    hq = Subnet(scope_id=scope.id, cidr="10.5.0.0/24", site="HQ", site_id=site.id)
    hq_child = Subnet(scope_id=scope.id, cidr="10.5.0.0/28")   # unlabelled: inherits HQ
    bare = Subnet(scope_id=scope.id, cidr="10.6.0.0/24")       # no site anywhere above it
    db_session.add_all([hq, hq_child, bare])
    db_session.flush()

    def eol_host(ip, *subnets):
        h = models.Host(project_id=test_project.id, ip_address=ip, state="up", os_name="Windows XP")
        db_session.add(h)
        db_session.flush()
        for sn in subnets:
            db_session.add(HostSubnetMapping(host_id=h.id, subnet_id=sn.id))

    eol_host("10.5.0.50", hq)
    eol_host("10.5.0.5", hq, hq_child)      # most-specific subnet has no site; still HQ
    eol_host("10.6.0.1", bare)
    eol_host("10.6.0.2", bare)
    eol_host("172.16.0.1")                  # outside every scoped subnet: unmapped, not unassigned
    db_session.commit()

    hm = compute_posture(db_session, test_project.id, use_cache=False)["heatmap"]
    lifecycle = next(r for r in hm["rows"] if r["family"] == "lifecycle_patching")
    cell = next(c for c in lifecycle["cells"] if c["segment"] == "unassigned")
    assert cell["affected"] == 2

    def ips(q):
        r = client.get(f"/api/v1/projects/{test_project.id}/hosts/", params={"q": q})
        assert r.status_code == 200, r.text
        return {i["ip_address"] for i in r.json()["items"]}

    assert ips("has:eol site:none") == {"10.6.0.1", "10.6.0.2"}
    # `none` ORs with names like any other value.
    assert ips("has:eol site:HQ,none") == {"10.5.0.50", "10.5.0.5", "10.6.0.1", "10.6.0.2"}


def test_posture_output_validates_against_response_model(db_session, test_project, test_user):
    """The endpoint's Pydantic response_model must accept a real compute_posture
    dict without dropping fields the frontend reads (extra="allow"), for every
    label state — including the new insufficient_evidence."""
    from app.api.v1.endpoints.posture import PostureResponse

    # insufficient_evidence: an owned, reviewed finding but NO scan evidence —
    # no action/assess signal fires, so the evidence gate decides the label.
    host = _host(db_session, test_project.id, "10.0.0.40")
    db_session.add(models.HostFollow(
        host_id=host.id, user_id=test_user.id, status=models.FollowStatus.REVIEWED.value,
    ))
    _finding(db_session, test_project.id, severity="low", owner_id=test_user.id, host=host)
    db_session.commit()
    out = compute_posture(db_session, test_project.id, use_cache=False)
    assert out["label"] == "insufficient_evidence"

    model = PostureResponse.model_validate(out)
    dumped = model.model_dump()
    # Round-trips every top-level key (extra="allow" keeps the ones not on the model).
    assert set(dumped) == set(out)
    assert dumped["systemic"] == out["systemic"]
    assert dumped["headline"] == out["headline"]
