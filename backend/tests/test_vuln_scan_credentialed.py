"""Whether the vulnerability scan authenticated, shown BESIDE "assessed"
(review 2026-10-01).

"Assessed for vulnerabilities" keeps its definition: any vulnerability-scanner
run counts.  What is added is one fact next to it — yes / no / not stated —
on the host detail, as three counts on the Evidence page that add up to the
assessed count, and as the Hosts query ``vulnscan:`` that opens each count.
One definition (``evidence_service.vuln_scan_credentialed_condition``).
"""
from datetime import datetime, timezone

from sqlalchemy import event

from app.db import models
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity, VulnerabilitySource
from app.services.evidence_service import (
    assessed_host_ids, compute_evidence_coverage, vuln_scan_credentialed_counts,
)
from app.services.host_assessment_service import host_assessment


def _scan(db, project_id, tool):
    scan = models.Scan(project_id=project_id, filename=f"{tool}.out", tool_name=tool)
    db.add(scan)
    db.flush()
    return scan


def _host(db, project_id, ip, *seen):
    """A host, and one history row per ``(scan, credentialed)``."""
    h = models.Host(project_id=project_id, ip_address=ip, state="up")
    h.last_seen = datetime.now(timezone.utc)
    db.add(h)
    db.flush()
    for scan, credentialed in seen:
        db.add(models.HostScanHistory(host_id=h.id, scan_id=scan.id, credentialed=credentialed))
    db.flush()
    return h


def _estate(db, project_id):
    n1, n2 = _scan(db, project_id, "Nessus"), _scan(db, project_id, "nessus")
    openvas, nmap, nikto = (_scan(db, project_id, t) for t in ("openvas", "nmap", "nikto"))
    hosts = {
        # One scan authenticated: yes — whatever another scan said.
        "yes": _host(db, project_id, "10.9.0.1", (n1, False), (n2, True)),
        "yes_plain": _host(db, project_id, "10.9.0.2", (n1, True)),
        # A scan says it did not, none says it did.
        "no": _host(db, project_id, "10.9.0.3", (n1, False), (openvas, None)),
        # Assessed, nobody said: an older import / OpenVAS.
        "unstated_scan": _host(db, project_id, "10.9.0.4", (openvas, None)),
        # Assessed through a vulnerability row only (no scanner run).
        "unstated_row": _host(db, project_id, "10.9.0.5", (nikto, None)),
        # Not assessed: a port scan's history row never counts, even if some
        # tool set the flag on it.
        "not_assessed": _host(db, project_id, "10.9.0.6", (nmap, True)),
    }
    db.add(Vulnerability(host_id=hosts["unstated_row"].id, scan_id=nikto.id, title="x",
                         severity=VulnerabilitySeverity.LOW, source=VulnerabilitySource.NESSUS))
    db.commit()
    return hosts


def test_host_assessment_says_whether_the_scan_authenticated(db_session, test_project):
    hosts = _estate(db_session, test_project.id)
    got = {k: host_assessment(db_session, h) for k, h in hosts.items()}

    assert {k: a["vuln_scan_credentialed"] for k, a in got.items()} == {
        "yes": "yes", "yes_plain": "yes", "no": "no",
        "unstated_scan": "not_stated", "unstated_row": "not_stated",
        "not_assessed": None,
    }
    # The definition of "assessed" did not move: an unauthenticated scan and
    # one that said nothing are assessments all the same.
    assert {k: a["vuln_assessed"] for k, a in got.items()} == {
        "yes": True, "yes_plain": True, "no": True,
        "unstated_scan": True, "unstated_row": True, "not_assessed": False,
    }


def test_host_detail_carries_it(client, db_session, test_project):
    hosts = _estate(db_session, test_project.id)
    r = client.get(f"/api/v1/projects/{test_project.id}/hosts/{hosts['no'].id}")
    assert r.status_code == 200, r.text
    assert r.json()["assessment"]["vuln_scan_credentialed"] == "no"
    r = client.get(f"/api/v1/projects/{test_project.id}/hosts/{hosts['not_assessed'].id}")
    assert r.json()["assessment"]["vuln_scan_credentialed"] is None


def test_evidence_counts_add_up_to_the_assessed_hosts(client, db_session, test_project):
    _estate(db_session, test_project.id)
    counts = vuln_scan_credentialed_counts(db_session, test_project.id)
    assert counts == {"credentialed": 2, "not_credentialed": 1, "credentials_not_stated": 2}
    assessed = assessed_host_ids(db_session, test_project.id, ["vuln_assessment"])["vuln_assessment"]
    assert sum(counts.values()) == len(assessed) == 5

    r = client.get(f"/api/v1/projects/{test_project.id}/posture/evidence")
    assert r.status_code == 200, r.text
    domain = next(d for d in r.json()["domains"] if d["key"] == "vuln_assessment")
    assert domain["coverage"]["numerator"] == 5 and domain["coverage"]["denominator"] == 6
    assert domain["credentialed"] == counts
    # No other domain grows the block.
    assert all("credentialed" not in d for d in r.json()["domains"] if d["key"] != "vuln_assessment")


def test_no_block_when_nothing_is_assessed(db_session, test_project):
    _host(db_session, test_project.id, "10.9.1.1", (_scan(db_session, test_project.id, "nmap"), None))
    db_session.commit()
    domain = next(d for d in compute_evidence_coverage(db_session, test_project.id)["domains"]
                  if d["key"] == "vuln_assessment")
    assert "credentialed" not in domain


def test_counts_are_one_statement_and_stay_in_the_project(db_session, test_project):
    from app.db.models_project import Project

    _estate(db_session, test_project.id)
    other = Project(name="other", slug="other-cred")
    db_session.add(other)
    db_session.flush()
    _host(db_session, other.id, "10.9.0.1", (_scan(db_session, other.id, "nessus"), True))
    db_session.commit()
    project_id = test_project.id  # loaded before counting statements

    statements = []
    engine = db_session.get_bind()
    listener = lambda *a: statements.append(a[2])  # noqa: E731
    event.listen(engine, "before_cursor_execute", listener)
    try:
        counts = vuln_scan_credentialed_counts(db_session, project_id)
    finally:
        event.remove(engine, "before_cursor_execute", listener)
    assert counts["credentialed"] == 2
    assert len([s for s in statements if s.lstrip().upper().startswith("SELECT")]) == 1


def test_each_count_opens_its_exact_hosts(client, db_session, test_project):
    hosts = _estate(db_session, test_project.id)

    def ips(q):
        r = client.get(f"/api/v1/projects/{test_project.id}/hosts/", params={"q": q, "limit": 50})
        assert r.status_code == 200, r.text
        body = r.json()
        return sorted(h["ip_address"] for h in (body["items"] if isinstance(body, dict) else body))

    assert ips("vulnscan:credentialed") == [hosts["yes"].ip_address, hosts["yes_plain"].ip_address]
    assert ips("vulnscan:uncredentialed") == [hosts["no"].ip_address]
    assert ips("vulnscan:unstated") == [hosts["unstated_scan"].ip_address, hosts["unstated_row"].ip_address]
    # A host nobody assessed is in none of the three — not "unstated".
    assert hosts["not_assessed"].ip_address not in ips("vulnscan:credentialed,uncredentialed,unstated")
    assert ips("NOT vulnscan:credentialed,uncredentialed,unstated") == [hosts["not_assessed"].ip_address]


def test_an_unknown_vulnscan_value_is_refused(client, test_project):
    r = client.get(f"/api/v1/projects/{test_project.id}/hosts/", params={"q": "vulnscan:maybe"})
    assert r.status_code == 400, r.text
    assert "vulnscan" in r.text
