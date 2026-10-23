"""Review 2026-10-22, A-C2 — "every host carrying this issue" is the STORED key.

``FindingService._issue_host_ids`` matched a ``cve:`` key on ``cve_id`` and any
other key on the promoted row's ``plugin_id``.  So an issue two tools reported
under a ``check:`` or ``title:`` key was promoted on one tool's hosts (the
client report under-listed the affected systems, and the other hosts stayed
"waiting" on Scanner observations), and a ``cve:`` promotion took rows whose
key is a ``check:`` — hosts of a different issue.  It now reads the
Observations list's own condition (``issue_rows_condition``).

Each case below fails on the old fan-out.
"""
from datetime import datetime, timezone

import pytest
from sqlalchemy import case

from app.db import models
from app.db.models import Host
from app.db.models_findings import FindingHost
from app.db.models_project import Project
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity
from app.services.engagement_metrics_service import observation_judged_on_host
from app.services.finding_service import FindingService


@pytest.fixture
def scan(db_session, test_project):
    s = models.Scan(project_id=test_project.id, filename="fixture.xml", scan_type="nessus", tool_name="nessus")
    db_session.add(s)
    db_session.commit()
    return s


def _host(db, project_id, ip):
    now = datetime.now(timezone.utc)
    h = Host(project_id=project_id, ip_address=ip, state="up", first_seen=now, last_seen=now)
    db.add(h)
    db.commit()
    return h


def _vuln(db, host, scan, **over):
    v = Vulnerability(
        host_id=host.id, scan_id=scan.id,
        title=over.pop("title", "Anonymous FTP enabled"),
        severity=over.pop("severity", VulnerabilitySeverity.HIGH),
        source=over.pop("source", "nessus"),
        **over,
    )
    db.add(v)
    db.commit()
    return v


def _attached(db, finding):
    return {hid for (hid,) in db.query(FindingHost.host_id).filter(FindingHost.finding_id == finding.id)}


def _promote_issue_wide(db, project, user, vuln):
    svc = FindingService(db)
    preview = svc.preview_vulnerability_promotion(vuln=vuln, project_id=project.id)
    finding, _ = svc.promote_vulnerability(vuln=vuln, project_id=project.id, actor_id=user.id)
    db.commit()
    return preview, finding


def _unjudged_rows_of(db, project_id, issue_key):
    return (
        db.query(Vulnerability.id)
        .join(Host, Host.id == Vulnerability.host_id)
        .filter(Host.project_id == project_id, Vulnerability.issue_key == issue_key)
        .filter(case((observation_judged_on_host(), False), else_=True))
        .count()
    )


def test_a_check_issue_two_tools_reported_is_promoted_on_both_tools_hosts(db_session, test_project, test_user, scan):
    a = _host(db_session, test_project.id, "10.22.0.1")
    b = _host(db_session, test_project.id, "10.22.0.2")
    nessus = _vuln(db_session, a, scan, source="nessus", plugin_id="10079", check_id="ftp_anonymous",
                   cve_id="CVE-1999-0497")
    _vuln(db_session, b, scan, source="nmap", plugin_id="ftp-anon", check_id="ftp_anonymous",
          title="ftp-anon: Anonymous FTP login allowed")
    assert nessus.issue_key == "check:ftp_anonymous"

    preview, finding = _promote_issue_wide(db_session, test_project, test_user, nessus)

    assert _attached(db_session, finding) == {a.id, b.id}
    assert preview["affected_host_count"] == 2
    assert _unjudged_rows_of(db_session, test_project.id, "check:ftp_anonymous") == 0


def test_a_title_issue_two_tools_reported_is_promoted_on_both_tools_hosts(db_session, test_project, test_user, scan):
    a = _host(db_session, test_project.id, "10.22.1.1")
    b = _host(db_session, test_project.id, "10.22.1.2")
    first = _vuln(db_session, a, scan, source="nessus", plugin_id="111", title="Weak SSH key exchange")
    _vuln(db_session, b, scan, source="openvas", plugin_id="1.3.6.1.4.1.25623", title="Weak SSH key exchange")
    assert first.issue_key.startswith("title:")

    preview, finding = _promote_issue_wide(db_session, test_project, test_user, first)

    assert _attached(db_session, finding) == {a.id, b.id}
    assert preview["affected_host_count"] == 2
    assert _unjudged_rows_of(db_session, test_project.id, first.issue_key) == 0


def test_a_cve_issue_does_not_take_the_rows_of_a_check_issue_with_that_cve(db_session, test_project, test_user, scan):
    a = _host(db_session, test_project.id, "10.22.2.1")
    b = _host(db_session, test_project.id, "10.22.2.2")
    cve_row = _vuln(db_session, a, scan, plugin_id="1", cve_id="CVE-1999-0497", title="Some FTP advisory")
    check_row = _vuln(db_session, b, scan, plugin_id="2", cve_id="CVE-1999-0497", check_id="ftp_anonymous")
    assert (cve_row.issue_key, check_row.issue_key) == ("cve:CVE-1999-0497", "check:ftp_anonymous")

    preview, finding = _promote_issue_wide(db_session, test_project, test_user, cve_row)

    assert _attached(db_session, finding) == {a.id}
    assert preview["affected_host_count"] == 1


def test_one_scanners_cve_issue_still_covers_every_host(db_session, test_project, test_user, scan):
    """Old == new for the case the old fan-out got right: one CVE, any casing."""
    hosts = [_host(db_session, test_project.id, f"10.22.3.{i}") for i in range(1, 4)]
    first = _vuln(db_session, hosts[0], scan, plugin_id="9", cve_id="CVE-2024-4444")
    _vuln(db_session, hosts[1], scan, plugin_id="9", cve_id="CVE-2024-4444")
    _vuln(db_session, hosts[2], scan, source="nuclei", cve_id="cve-2024-4444", title="nuclei words")

    preview, finding = _promote_issue_wide(db_session, test_project, test_user, first)

    assert _attached(db_session, finding) == {h.id for h in hosts}
    assert preview["affected_host_count"] == 3


def test_another_projects_rows_of_the_issue_are_not_attached(db_session, test_project, test_user, scan):
    other = Project(name="other-2026-10-22", slug="other-2026-10-22")
    db_session.add(other)
    db_session.commit()
    other_scan = models.Scan(project_id=other.id, filename="o.xml", scan_type="nessus", tool_name="nessus")
    db_session.add(other_scan)
    db_session.commit()
    a = _host(db_session, test_project.id, "10.22.4.1")
    elsewhere = _host(db_session, other.id, "10.22.4.1")
    mine = _vuln(db_session, a, scan, plugin_id="1", check_id="ftp_anonymous")
    _vuln(db_session, elsewhere, other_scan, plugin_id="1", check_id="ftp_anonymous")

    _preview, finding = _promote_issue_wide(db_session, test_project, test_user, mine)

    assert _attached(db_session, finding) == {a.id}
