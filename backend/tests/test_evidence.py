"""Evidence coverage service + the per-domain posture label gate."""
from app.db import models
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity, VulnerabilitySource
from app.services.evidence_service import compute_evidence_coverage, has_minimum_assessment
from app.services.posture_service import compute_posture


def _host(db, project_id, ip, **kw):
    h = models.Host(project_id=project_id, ip_address=ip, state="up", **kw)
    db.add(h)
    db.flush()
    return h


def _scan(db, project_id, tool="nmap"):
    s = models.Scan(project_id=project_id, filename="s", tool_name=tool, scan_type="nmap")
    db.add(s)
    db.flush()
    return s


def test_domains_eligible_and_assessed(db_session, test_project):
    pid = test_project.id
    scan = _scan(db_session, pid)
    # Host A: web + service; Host B: SMB port, no auth evidence; Host C: bare.
    a = _host(db_session, pid, "10.0.0.1")
    b = _host(db_session, pid, "10.0.0.2")
    _host(db_session, pid, "10.0.0.3")
    db_session.add(models.Port(host_id=a.id, port_number=443, protocol="tcp", state="open", service_name="https"))
    db_session.add(models.Port(host_id=b.id, port_number=445, protocol="tcp", state="open"))
    db_session.add(models.WebInterface(host_id=a.id, scan_id=scan.id, project_id=pid, url="https://a", protocol="https"))
    db_session.add(Vulnerability(host_id=a.id, scan_id=scan.id, title="v", severity=VulnerabilitySeverity.HIGH,
                                 source=VulnerabilitySource.NESSUS, plugin_id="1"))
    db_session.commit()

    out = compute_evidence_coverage(db_session, pid)
    assert out["total_hosts"] == 3
    dom = {d["key"]: d["coverage"] for d in out["domains"]}

    # Port discovery: 2 of 3 hosts have a port.
    assert dom["port_discovery"]["numerator"] == 2 and dom["port_discovery"]["denominator"] == 3
    # Service: 1 of the 2 port-bearing hosts has a service name.
    assert dom["service_detection"]["numerator"] == 1 and dom["service_detection"]["denominator"] == 2
    # Vuln: 1 of 3.
    assert dom["vuln_assessment"]["numerator"] == 1 and dom["vuln_assessment"]["denominator"] == 3
    # Web: host A is eligible (443) and assessed (web interface) → 1/1.
    assert dom["web_tls"]["numerator"] == 1 and dom["web_tls"]["denominator"] == 1
    # Auth: host B eligible (445), but no SMB-signing/NetExec evidence → 0/1.
    assert dom["auth_smb_ad"]["numerator"] == 0 and dom["auth_smb_ad"]["denominator"] == 1

    tools = {t["tool"] for t in out["contributing_tools"]}
    assert "nmap" in tools


def test_minimum_assessment_gate(db_session, test_project, test_user):
    pid = test_project.id
    # Discovery-only: a host with a bare port, no service, no vuln. Reviewed, so
    # no under-review assess signal masks the evidence gate.
    _scan(db_session, pid)
    h = _host(db_session, pid, "10.0.0.9")
    db_session.add(models.HostFollow(
        host_id=h.id, user_id=test_user.id, status=models.FollowStatus.REVIEWED.value,
    ))
    db_session.add(models.Port(host_id=h.id, port_number=80, protocol="tcp", state="open"))
    db_session.commit()
    assert has_minimum_assessment(db_session, pid) is False
    # A discovery-only estate must not read as reassuring.
    out = compute_posture(db_session, pid, use_cache=False)
    assert out["label"] == "insufficient_evidence"

    # Add a service → minimum assessment met.
    db_session.add(models.Port(host_id=h.id, port_number=443, protocol="tcp", state="open", service_name="https"))
    db_session.commit()
    assert has_minimum_assessment(db_session, pid) is True
