"""The inspector must show that a vulnerability is already covered (v2.239.1).

Operators were re-promoting vulnerabilities because nothing on the row said it
had been promoted. Two separate causes, both silent:

1. ``serialize_vulnerability`` emitted ``finding_id``, but the
   ``HostVulnerability`` response model had no such field, so FastAPI dropped
   it on every request. The badge therefore rendered only from in-browser
   session state and vanished on reload.

2. Even with that fixed, a finding covers an ISSUE across hosts. Promoting
   from host A attaches host B, but B's own scanner row was never itself
   promoted — so B's row still looked un-promoted and invited a duplicate.

The third test pins preview against reality: the confirmation dialog's counts
have to match what promoting actually does.
"""

from datetime import datetime, timezone

import pytest

from app.db import models
from app.db.models import Host
from app.db.models_findings import Finding, FindingHost
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity
from app.services.finding_service import FindingService


@pytest.fixture
def scan(db_session, test_project):
    s = models.Scan(
        project_id=test_project.id, filename="fixture.xml",
        scan_type="nessus", tool_name="nessus",
    )
    db_session.add(s)
    db_session.commit()
    db_session.refresh(s)
    return s


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
        host_id=host.id, scan_id=scan_id,
        title=over.pop("title", "OpenSSL out of date"),
        severity=over.pop("severity", VulnerabilitySeverity.HIGH),
        source=over.pop("source", "nessus"),
        **over,
    )
    db.add(v)
    db.commit()
    db.refresh(v)
    return v


def _vulns_in_response(client, project_id, host_id):
    r = client.get(f"/api/v1/projects/{project_id}/hosts/{host_id}")
    assert r.status_code == 200, r.text
    return r.json()["vulnerabilities"]


def test_promoted_vuln_reports_its_finding_in_the_host_response(
    client, db_session, test_project, test_user, scan,
):
    """The regression that made the badge disappear on reload.

    ``finding_id`` was serialized and then dropped by response validation, so
    the API said "not promoted" about a vulnerability that plainly was.
    """
    host = _host(db_session, test_project.id, "10.0.0.1")
    vuln = _vuln(db_session, host, scan.id, cve_id="CVE-2024-1111")

    finding = FindingService(db_session).promote_vulnerability(
        vuln=vuln, project_id=test_project.id, actor_id=test_user.id,
    )
    db_session.commit()

    row = _vulns_in_response(client, test_project.id, host.id)[0]
    assert row["finding_id"] == finding.id
    assert row["finding_match"] == "vuln", "this row IS the promoted source"
    assert row["finding_status"] == finding.status


def test_same_issue_on_another_host_reads_as_covered_not_unpromoted(
    client, db_session, test_project, test_user, scan,
):
    """Promoting from host A must make host B's row read as covered.

    The fan-out already attached B to the finding; only the UI didn't know,
    which is exactly the state in which an operator promotes a second time.
    """
    host_a = _host(db_session, test_project.id, "10.0.0.1")
    host_b = _host(db_session, test_project.id, "10.0.0.2")
    vuln_a = _vuln(db_session, host_a, scan.id, cve_id="CVE-2024-2222")
    # Same issue, different host, different scanner.
    _vuln(db_session, host_b, scan.id, cve_id="CVE-2024-2222",
          source="openvas", title="OpenSSL is outdated (GreenBone wording)")

    finding = FindingService(db_session).promote_vulnerability(
        vuln=vuln_a, project_id=test_project.id, actor_id=test_user.id,
    )
    db_session.commit()

    row_b = _vulns_in_response(client, test_project.id, host_b.id)[0]
    assert row_b["finding_id"] == finding.id, (
        "host B's row was never promoted itself, but the finding covers it"
    )
    assert row_b["finding_match"] == "issue"


def test_uncovered_vuln_still_reads_as_uncovered(
    client, db_session, test_project, scan,
):
    """Guards the inverse: coverage must not be claimed where none exists."""
    host = _host(db_session, test_project.id, "10.0.0.3")
    _vuln(db_session, host, scan.id, cve_id="CVE-2024-3333")

    row = _vulns_in_response(client, test_project.id, host.id)[0]
    assert row["finding_id"] is None
    assert row["finding_match"] is None


def test_preview_blast_radius_matches_what_promotion_actually_attaches(
    db_session, test_project, test_user, scan,
):
    """The dialog's numbers must be the action's numbers.

    Preview fanned out on ``plugin_id`` while promote fanned out on the issue
    key, so an issue reported by two scanners previewed smaller than it landed.
    A confirmation dialog that understates its own blast radius is worse than
    no dialog.
    """
    svc = FindingService(db_session)
    hosts = [_host(db_session, test_project.id, f"10.0.1.{i}") for i in range(1, 4)]

    # One issue (same CVE) seen by a different scanner on each host — so a
    # plugin_id-keyed fan-out would find only the first.
    source_vuln = _vuln(
        db_session, hosts[0], scan.id, cve_id="CVE-2024-4444",
        source="nessus", plugin_id="11111",
    )
    _vuln(db_session, hosts[1], scan.id, cve_id="CVE-2024-4444",
          source="openvas", plugin_id="22222", title="Different wording")
    _vuln(db_session, hosts[2], scan.id, cve_id="CVE-2024-4444",
          source="openvas", plugin_id="33333", title="Third wording")

    preview = svc.preview_vulnerability_promotion(
        vuln=source_vuln, project_id=test_project.id,
    )
    assert preview["affected_host_count"] == 3, preview
    assert preview["new_host_count"] == 3, "nothing attached yet"
    assert preview["already_promoted"] is False

    finding = svc.promote_vulnerability(
        vuln=source_vuln, project_id=test_project.id, actor_id=test_user.id,
    )
    db_session.commit()

    attached = (
        db_session.query(FindingHost).filter(FindingHost.finding_id == finding.id).count()
    )
    assert attached == preview["affected_host_count"], (
        "preview promised a blast radius promotion didn't deliver"
    )

    # Re-previewing now reports the finding and zero *new* hosts — promoting
    # again would only re-attach evidence.
    after = svc.preview_vulnerability_promotion(
        vuln=source_vuln, project_id=test_project.id,
    )
    assert after["already_promoted"] is True
    assert after["finding_id"] == finding.id
    assert after["new_host_count"] == 0, after
