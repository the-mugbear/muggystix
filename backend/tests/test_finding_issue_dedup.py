"""
One issue → one Finding, however many scanners reported it (v2.236.0).

`Finding.vuln_id` is a single FK, so promoting the Nessus row and the
GreenBone row for the same problem produced two Findings — and two entries in
the client report. Grouping in the host inspector made that duplication less
likely to happen by accident, but it couldn't prevent it: the dedup has to
live where the record is created.

The fix keys the Finding on the ISSUE (`services.vuln_identity.issue_key` —
CVE, else normalised title; never the scanner) and records each contributing
scanner row as evidence.
"""

from datetime import datetime, timezone

import pytest

from app.db.models import Host
from app.db.models_findings import Finding, FindingVulnerability
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity
from app.services.finding_service import FindingService


def _host(db, project_id, ip):
    h = Host(
        project_id=project_id, ip_address=ip, state="up",
        first_seen=datetime.now(timezone.utc), last_seen=datetime.now(timezone.utc),
    )
    db.add(h)
    db.commit()
    db.refresh(h)
    return h


def _vuln(db, host, scan_id, **over):
    v = Vulnerability(
        host_id=host.id,
        scan_id=scan_id,
        title=over.pop("title", "Some issue"),
        severity=over.pop("severity", VulnerabilitySeverity.HIGH),
        source=over.pop("source", "nessus"),
        **over,
    )
    db.add(v)
    db.commit()
    db.refresh(v)
    return v


@pytest.fixture
def scan_id(db_session, test_project):
    from app.db import models

    scan = models.Scan(
        project_id=test_project.id, filename="fixture.xml",
        scan_type="nessus", tool_name="nessus",
    )
    db_session.add(scan)
    db_session.commit()
    db_session.refresh(scan)
    return scan.id


def test_two_scanners_one_cve_produce_one_finding(
    db_session, test_project, test_user, scan_id
):
    """The reported problem, end to end."""
    host = _host(db_session, test_project.id, "10.60.0.1")
    nessus = _vuln(
        db_session, host, scan_id, source="nessus", cve_id="CVE-2021-44228",
        plugin_id="156057", title="Apache Log4j Remote Code Execution",
    )
    openvas = _vuln(
        db_session, host, scan_id, source="openvas", cve_id="CVE-2021-44228",
        plugin_id="1.3.6.1.4.1.25623.1.0.117842",
        title="Apache Log4j RCE Vulnerability (Log4Shell)",
    )

    svc = FindingService(db_session)
    first = svc.promote_vulnerability(
        vuln=nessus, project_id=test_project.id, actor_id=test_user.id,
    )
    second = svc.promote_vulnerability(
        vuln=openvas, project_id=test_project.id, actor_id=test_user.id,
    )
    db_session.commit()

    assert first.id == second.id, (
        "the same CVE from two scanners must converge on one finding, or the "
        "client report double-counts it"
    )
    assert db_session.query(Finding).count() == 1

    evidence = (
        db_session.query(FindingVulnerability)
        .filter(FindingVulnerability.finding_id == first.id)
        .all()
    )
    assert {e.vuln_id for e in evidence} == {nessus.id, openvas.id}, (
        "both scanner rows must be recorded — corroboration is the point"
    )


def test_distinct_cves_stay_distinct_findings(
    db_session, test_project, test_user, scan_id
):
    """Dedup must not over-reach: different CVEs are different problems."""
    host = _host(db_session, test_project.id, "10.60.0.2")
    a = _vuln(db_session, host, scan_id, cve_id="CVE-2021-44228", title="Log4j RCE")
    b = _vuln(db_session, host, scan_id, cve_id="CVE-2021-45046", title="Log4j RCE")

    svc = FindingService(db_session)
    fa = svc.promote_vulnerability(vuln=a, project_id=test_project.id, actor_id=test_user.id)
    fb = svc.promote_vulnerability(vuln=b, project_id=test_project.id, actor_id=test_user.id)
    db_session.commit()

    assert fa.id != fb.id
    assert db_session.query(Finding).count() == 2


def test_cveless_findings_dedup_on_normalised_title(
    db_session, test_project, test_user, scan_id
):
    host = _host(db_session, test_project.id, "10.60.0.3")
    a = _vuln(
        db_session, host, scan_id, source="nessus",
        title="SSL/TLS: Weak Cipher Suites Supported",
    )
    b = _vuln(
        db_session, host, scan_id, source="openvas",
        title="ssl tls weak cipher suites supported",
    )

    svc = FindingService(db_session)
    fa = svc.promote_vulnerability(vuln=a, project_id=test_project.id, actor_id=test_user.id)
    fb = svc.promote_vulnerability(vuln=b, project_id=test_project.id, actor_id=test_user.id)
    db_session.commit()

    assert fa.id == fb.id
    assert db_session.query(Finding).count() == 1


def test_similar_titles_are_not_merged(
    db_session, test_project, test_user, scan_id
):
    """No fuzzy matching. A wrong merge hides a real finding."""
    host = _host(db_session, test_project.id, "10.60.0.4")
    a = _vuln(db_session, host, scan_id, title="TLS 1.0 Protocol Detected")
    b = _vuln(db_session, host, scan_id, title="TLS 1.1 Protocol Detected")

    svc = FindingService(db_session)
    fa = svc.promote_vulnerability(vuln=a, project_id=test_project.id, actor_id=test_user.id)
    fb = svc.promote_vulnerability(vuln=b, project_id=test_project.id, actor_id=test_user.id)
    db_session.commit()

    assert fa.id != fb.id


def test_fanout_covers_hosts_only_the_other_scanner_saw(
    db_session, test_project, test_user, scan_id
):
    """The second bug of the same shape: the cross-host fan-out keyed on
    plugin_id, which is scanner-specific — so promoting from Nessus silently
    left out hosts only GreenBone had scanned."""
    seen_by_both = _host(db_session, test_project.id, "10.60.1.1")
    openvas_only = _host(db_session, test_project.id, "10.60.1.2")

    nessus = _vuln(
        db_session, seen_by_both, scan_id, source="nessus",
        cve_id="CVE-2020-9999", plugin_id="11111", title="Heap overflow",
    )
    _vuln(
        db_session, openvas_only, scan_id, source="openvas",
        cve_id="CVE-2020-9999", plugin_id="2.2.2.2", title="Heap overflow (remote)",
    )

    svc = FindingService(db_session)
    finding = svc.promote_vulnerability(
        vuln=nessus, project_id=test_project.id, actor_id=test_user.id,
    )
    db_session.commit()
    db_session.refresh(finding)

    attached = {fh.host_id for fh in finding.hosts}
    assert attached == {seen_by_both.id, openvas_only.id}, (
        "a host only the other scanner saw must still be covered by the finding"
    )


def test_promoting_the_same_row_twice_is_still_idempotent(
    db_session, test_project, test_user, scan_id
):
    host = _host(db_session, test_project.id, "10.60.0.5")
    v = _vuln(db_session, host, scan_id, cve_id="CVE-2019-1234")

    svc = FindingService(db_session)
    a = svc.promote_vulnerability(vuln=v, project_id=test_project.id, actor_id=test_user.id)
    b = svc.promote_vulnerability(vuln=v, project_id=test_project.id, actor_id=test_user.id)
    db_session.commit()

    assert a.id == b.id
    assert db_session.query(Finding).count() == 1
    # Evidence rows must not duplicate either.
    assert (
        db_session.query(FindingVulnerability)
        .filter(FindingVulnerability.finding_id == a.id)
        .count()
        == 1
    )


def test_dedup_does_not_cross_projects(
    db_session, test_project, test_user, scan_id
):
    """Two clients can have the same CVE; they must not share a finding."""
    from app.db.models_project import Project

    other = Project(name="other-client", slug="other-client")
    db_session.add(other)
    db_session.commit()
    db_session.refresh(other)

    from app.db import models
    other_scan = models.Scan(
        project_id=other.id, filename="other.xml", scan_type="nessus", tool_name="nessus",
    )
    db_session.add(other_scan)
    db_session.commit()
    db_session.refresh(other_scan)

    h1 = _host(db_session, test_project.id, "10.60.2.1")
    h2 = _host(db_session, other.id, "10.60.2.1")
    v1 = _vuln(db_session, h1, scan_id, cve_id="CVE-2022-1111")
    v2 = _vuln(db_session, h2, other_scan.id, cve_id="CVE-2022-1111")

    svc = FindingService(db_session)
    f1 = svc.promote_vulnerability(vuln=v1, project_id=test_project.id, actor_id=test_user.id)
    f2 = svc.promote_vulnerability(vuln=v2, project_id=other.id, actor_id=test_user.id)
    db_session.commit()

    assert f1.id != f2.id


# ---------------------------------------------------------------------------
# One key, one implementation
# ---------------------------------------------------------------------------

def test_the_issue_key_is_served_to_the_ui(
    client, db_session, test_project, scan_id
):
    """The inspector groups by this value rather than recomputing it. If the
    field stopped being served the UI would silently fall back to its own
    derivation, and the grouping shown could diverge from the dedup performed."""
    host = _host(db_session, test_project.id, "10.60.3.1")
    _vuln(db_session, host, scan_id, cve_id="CVE-2023-5555", title="Some issue")

    resp = client.get(f"/api/v1/projects/{test_project.id}/hosts/{host.id}")
    assert resp.status_code == 200, resp.text
    vulns = resp.json()["vulnerabilities"]
    assert vulns, "fixture vuln should be serialized"
    assert vulns[0]["issue_key"] == "cve:CVE-2023-5555"


def test_issue_key_matches_the_frontend_normalisation_contract():
    """Pinned vectors shared with frontend/src/utils/vulnGrouping.ts. The
    frontend prefers the served key, so these only have to agree for the
    fallback path — but a silent divergence here is how the two would drift."""
    from app.services.vuln_identity import issue_key, normalize_title

    assert normalize_title("Nessus: Weak Ciphers") == "weak ciphers"
    assert normalize_title("Weak Ciphers (OpenVAS)") == "weak ciphers"
    assert normalize_title("SSL/TLS: Weak Cipher Suites Supported") == (
        "ssl tls weak cipher suites supported"
    )
    # Version numbers survive — they distinguish genuinely different findings.
    assert normalize_title("TLS 1.0 Detected") != normalize_title("TLS 1.1 Detected")

    assert issue_key(cve_id="cve-2021-44228", title="x") == "cve:CVE-2021-44228"
    assert issue_key(cve_id=None, title="Weak Ciphers") == "title:weak ciphers"
    assert issue_key(cve_id=None, title=None, row_id=7) == "row:7"
