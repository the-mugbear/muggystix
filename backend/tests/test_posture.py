"""Security Posture composition — the parts most likely to regress: the
deterministic label, the headline measures, and that the label/reasons/
priorities share one signal pass (so they can't disagree).
"""
from __future__ import annotations

from app.db import models
from app.db.models_findings import Finding, FindingHost, FindingStatusHistory
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


def test_unowned_critical_is_action_required(db_session, test_project):
    """An unowned critical active finding escalates to action_required, and the
    top reason names it. The headline severity breakdown reflects it."""
    host = _host(db_session, test_project.id, "10.0.0.10")
    _finding(db_session, test_project.id, severity="critical", owner_id=None, host=host)
    db_session.commit()

    out = compute_posture(db_session, test_project.id, use_cache=False)
    assert out["label"] == "action_required"
    assert out["headline"]["active_exposure"]["by_severity"]["critical"] == 1
    assert out["headline"]["ownership"]["unowned"] == 1
    # The label, reasons, and priorities share one pass — the ownership signal
    # is both the top reason and the top priority.
    assert any("unowned" in r["text"].lower() for r in out["reasons"])
    assert out["priorities"][0]["kind"] == "ownership"


def test_owned_reviewed_finding_no_urgent_signals(db_session, test_project, test_user):
    """A single owned, non-critical finding on a reviewed host, with no systemic
    spread AND scan evidence present, produces no action/assess signals."""
    host = _host(db_session, test_project.id, "10.0.0.20")
    db_session.add(models.HostFollow(
        host_id=host.id, user_id=test_user.id, status=models.FollowStatus.REVIEWED.value,
    ))
    # Scan evidence exists — the estate has actually been assessed.
    db_session.add(models.Scan(project_id=test_project.id, filename="s", tool_name="nmap", scan_type="nmap"))
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
        "label", "conclusion", "reasons", "remediation_flow", "headline",
        "priorities", "decisions", "sites", "systemic", "disposition", "evidence",
    }
    assert set(out["conclusion"]) >= {"text", "tone"}
    assert set(out["remediation_flow"]) >= {
        "remediated", "reopened", "active_age_bands", "active_total", "unowned_backlog",
    }
    assert set(out["remediation_flow"]["active_age_bands"]) == {"le_7d", "le_30d", "le_90d", "gt_90d"}
    assert set(out["headline"]) >= {
        "active_exposure", "review_coverage", "ownership", "systemic", "detected_exposure",
    }
    assert "adopted" in out["headline"]["systemic"]
    assert set(out["disposition"]) >= {"scanner_active", "non_scanner_active", "by_status"}
    assert set(out["decisions"]) >= {"pending_approvals", "blocked_sessions"}
    assert set(out["evidence"]) >= {"scan_count", "scan_staleness_days"}
    for p in out["priorities"]:
        assert set(p) >= {"kind", "title", "blast_radius", "action", "severity", "owner", "link"}


def test_remediation_flow_counts_remediated_and_reopened(db_session, test_project, test_user):
    """remediation_flow reports remediated + reopened + active-backlog age bands."""
    host = _host(db_session, test_project.id, "10.0.0.50")
    db_session.add(models.Scan(project_id=test_project.id, filename="s", tool_name="nmap", scan_type="nmap"))
    # One remediated finding.
    _finding(db_session, test_project.id, severity="medium", status="remediated", owner_id=test_user.id, host=host)
    # One reopened finding: a resolved -> active transition in its history.
    reopened = _finding(db_session, test_project.id, severity="high", status="open", owner_id=test_user.id, host=host)
    db_session.add(FindingStatusHistory(
        finding_id=reopened.id, from_status="remediated", to_status="open",
    ))
    db_session.commit()

    out = compute_posture(db_session, test_project.id, use_cache=False)
    rf = out["remediation_flow"]
    assert rf["remediated"] == 1
    assert rf["reopened"] == 1
    # The one active finding lands in a single age band.
    assert sum(rf["active_age_bands"].values()) == rf["active_total"] == 1


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
