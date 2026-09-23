"""One rule for "active" (review 2026-09-23 R7, B-Debt-3).

* The Hosts-list badge counts a finding on a host only while THAT host's
  endpoint is live: a host dismissed as a false positive on a confirmed
  finding kept counting it.
* The Overview's exposure counts a finding only while it is a result: one
  judged a false positive on every endpoint drove "action required" while
  Oversight and the client report left it out.
"""
from app.db import models
from app.db.models_findings import Finding, FindingHost
from app.services.attention_service import compute_project_attention


def _host(db, project, ip):
    h = models.Host(project_id=project.id, ip_address=ip, state="up")
    db.add(h)
    db.flush()
    return h


def _finding(db, project, hosts, severity="critical", status="confirmed"):
    f = Finding(project_id=project.id, title=f"{severity} thing", severity=severity, status=status, source="manual")
    db.add(f)
    db.flush()
    for host, host_status in hosts:
        db.add(FindingHost(finding_id=f.id, host_id=host.id, host_status=host_status))
    db.commit()
    return f


def test_the_hosts_badge_follows_the_hosts_own_endpoint(client, db_session, test_project):
    a, b = _host(db_session, test_project, "10.70.0.1"), _host(db_session, test_project, "10.70.0.2")
    _finding(db_session, test_project, [(a, "open"), (b, "false_positive")])
    rows = client.get(f"/api/v1/projects/{test_project.id}/hosts/").json()
    items = rows["items"] if isinstance(rows, dict) else rows
    counts = {h["ip_address"]: h["finding_count"] for h in items}
    assert counts == {"10.70.0.1": 1, "10.70.0.2": 0}


def test_a_finding_false_positive_everywhere_is_not_exposure(db_session, test_project):
    a = _host(db_session, test_project, "10.71.0.1")
    _finding(db_session, test_project, [(a, "false_positive")])
    _finding(db_session, test_project, [(a, "open")], severity="high")
    exposure = compute_project_attention(db_session, test_project.id)["exposure"]
    assert exposure["by_severity"]["critical"] == 0
    assert exposure["by_severity"]["high"] == 1
